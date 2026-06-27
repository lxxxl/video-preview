import os
import sys
import structlog
import libtorrent as lt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SNAPSHOT_DIR, TEMP_DIR, SNAPSHOT_SETTINGS, ADAPTIVE_DOWNGRADE
from storage.task_store import TaskStore
from core.torrent_parser import find_largest_video, compute_head_tail_pieces
from core.segment_extractor import read_pieces, assemble_segment, cleanup_segment
from core.snapshot_generator import generate_snapshot
from core.mp4_utils import get_keyframe_positions
import time

logger = structlog.get_logger(__name__)


class TaskCancelled(Exception):
    pass


def _is_cancelled(store, task_id):
    return store.get_task_status(task_id) == "cancelled"


def _make_cancel_check(store, task_id):
    def check():
        if _is_cancelled(store, task_id):
            raise TaskCancelled()
    return check


def _apply_adaptive_downgrade(video_size, sample_points, adj_pieces, output_width):
    """方案 D: 冷门/大文件自适应降级采样点和分辨率"""
    if not ADAPTIVE_DOWNGRADE.get("enabled"):
        return sample_points, adj_pieces, output_width

    for threshold, new_points, new_adj, new_width in ADAPTIVE_DOWNGRADE["tiers"]:
        if video_size >= threshold:
            logger.info(
                "adaptive_downgrade",
                video_size_gb=round(video_size / 1024**3, 1),
                original_points=len(sample_points),
                new_points=len(new_points),
                new_width=new_width,
                new_adj=new_adj,
            )
            return new_points, new_adj, new_width

    return sample_points, adj_pieces, output_width


def _configure_upload_slots(handle):
    """方案 A: torrent-level 上传限制，让 BT 客户端感知互惠，避免只下不传被惩罚"""
    try:
        handle.set_upload_limit(200 * 1024)   # 200 KB/s
    except Exception:
        pass


def process_task(task_id: str, magnet: str, sample_points: list[int], timeout: int):
    store = TaskStore()
    try:
        _run_task(store, task_id, magnet, sample_points, timeout)
    except TaskCancelled:
        logger.info("task_cancelled", task_id=task_id)
    except Exception as e:
        logger.error("task_failed", task_id=task_id, error=str(e))
        store.update_task(task_id, status="failed", error=str(e))
    finally:
        store.close()


def _run_task(store: TaskStore, task_id: str, magnet: str, sample_points: list[int], timeout: int):
    import shutil

    if _is_cancelled(store, task_id):
        raise TaskCancelled()

    cancel_check = _make_cancel_check(store, task_id)

    store.update_task(task_id, status="resolving_metadata")

    from core.torrent_parser import resolve_metadata
    handle = resolve_metadata(magnet, timeout=60, cancel_check=cancel_check)

    task_tmp = os.path.join(TEMP_DIR, task_id)
    try:
        video = find_largest_video(handle)

        # ── 方案 D: 自适应降级 ──
        sample_points, adj_pieces, snap_width = _apply_adaptive_downgrade(
            video.size, sample_points,
            SNAPSHOT_SETTINGS["adjacent_pieces"],
            SNAPSHOT_SETTINGS["output_width"],
        )

        # ── 方案 A: 上传槽位 ──
        _configure_upload_slots(handle)

        store.update_task(
            task_id,
            status="downloading",
            metadata_resolved=1,
            video_file=video.name,
            video_size_bytes=video.size,
        )

        task = store.get_task(task_id)
        info_hash = task["info_hash"] if task else task_id
        task_snap = os.path.join(SNAPSHOT_DIR, info_hash)
        os.makedirs(task_tmp, exist_ok=True)
        os.makedirs(task_snap, exist_ok=True)

        head_pieces, tail_pieces = compute_head_tail_pieces(
            video, SNAPSHOT_SETTINGS["head_bytes"], SNAPSHOT_SETTINGS["tail_bytes"]
        )
        num_pieces = video.total_pieces

        # Phase 1: Download head pieces
        priorities = [0] * num_pieces
        for p in head_pieces:
            if 0 <= p < num_pieces:
                priorities[p] = 7
        handle.prioritize_pieces(priorities)
        handle.unset_flags(lt.torrent_flags.upload_mode)
        handle.resume()

        deadline = time.time() + min(120, timeout)
        while time.time() < deadline:
            if _is_cancelled(store, task_id):
                raise TaskCancelled()
            if all(handle.have_piece(p) for p in head_pieces):
                break
            time.sleep(1)

        if not all(handle.have_piece(p) for p in head_pieces):
            store.update_task(task_id, status="failed", error="Head pieces download timeout")
            return

        head_data_dict = read_pieces(handle, head_pieces, timeout=30)
        head_path = os.path.join(task_tmp, "head.bin")
        assemble_segment(head_data_dict, video, head_path)
        head_bytes = open(head_path, "rb").read()

        # Phase 2: Parse keyframes and plan downloads
        keyframes = get_keyframe_positions(head_bytes)
        if keyframes:
            _download_with_keyframes(store, handle, video, task_id, sample_points,
                                     timeout, keyframes, head_pieces, head_bytes,
                                     task_tmp, task_snap, num_pieces,
                                     adj_pieces, snap_width)
        else:
            _download_byte_offset(store, handle, video, task_id, sample_points,
                                  timeout, head_pieces, head_bytes,
                                  task_tmp, task_snap, num_pieces)
    finally:
        from core.session_manager import SessionManager
        try:
            SessionManager().remove_torrent(handle, delete_files=True)
        except Exception:
            pass
        if os.path.isdir(task_tmp):
            shutil.rmtree(task_tmp, ignore_errors=True)


def _download_with_keyframes(store, handle, video, task_id, sample_points,
                              timeout, keyframes, head_pieces, head_bytes,
                              task_tmp, task_snap, num_pieces,
                              adj_pieces, snap_width):
    duration = keyframes[-1][0]

    sample_plan = {}
    all_sample_pieces = set()
    for pct in sample_points:
        target_time = duration * pct / 100
        closest_kf = min(keyframes, key=lambda k: abs(k[0] - target_time))
        kf_piece = closest_kf[1] // video.piece_length
        pieces = [p for p in range(kf_piece - adj_pieces, kf_piece + adj_pieces + 1)
                  if video.first_piece <= p <= video.last_piece]
        sample_plan[pct] = {"time": closest_kf[0], "offset": closest_kf[1], "pieces": pieces}
        all_sample_pieces.update(pieces)

    priorities = [0] * num_pieces
    for p in head_pieces:
        if 0 <= p < num_pieces:
            priorities[p] = 7
    for p in all_sample_pieces:
        if 0 <= p < num_pieces:
            priorities[p] = 6
    handle.prioritize_pieces(priorities)

    store.update_task(task_id, pieces_needed=len(all_sample_pieces))

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_cancelled(store, task_id):
            raise TaskCancelled()

        downloaded = sum(1 for p in all_sample_pieces if handle.have_piece(p))
        status = handle.status()
        store.update_task(
            task_id,
            pieces_downloaded=downloaded,
            download_speed_bps=int(status.download_rate),
            peers_connected=status.num_peers,
        )
        if downloaded == len(all_sample_pieces):
            break

        # Generate snapshots for ready sample points
        for pct, plan in sample_plan.items():
            if all(handle.have_piece(p) for p in plan["pieces"]):
                _generate_sparse_snapshot(
                    store, handle, video, task_id, pct, plan,
                    head_bytes, task_tmp, task_snap, snap_width
                )

        time.sleep(1)

    timed_out = time.time() >= deadline
    store.update_task(task_id, status="timeout" if timed_out else "completed")


def _generate_sparse_snapshot(store, handle, video, task_id, pct, plan,
                               head_bytes, task_tmp, task_snap, snap_width=None):
    task = store.get_task(task_id)
    if task:
        for s in task.get("snapshots", []):
            if s["percent"] == pct and s["status"] == "ready":
                return

    try:
        store.update_task(task_id, status="generating")
        pieces = plan["pieces"]
        piece_data = read_pieces(handle, pieces, timeout=30)

        sparse_path = os.path.join(task_tmp, f"sparse_{pct:02d}.mp4")
        with open(sparse_path, "wb") as f:
            f.write(head_bytes)
            for p in sorted(piece_data.keys()):
                piece_offset = p * video.piece_length - video.offset
                if piece_offset > len(head_bytes):
                    f.seek(piece_offset)
                    f.write(piece_data[p])

        snap_filename = f"snap_{pct:02d}.jpg"
        snap_path = os.path.join(task_snap, snap_filename)

        success = generate_snapshot(sparse_path, snap_path,
                                    seek_offset=plan["time"],
                                    width=snap_width)
        cleanup_segment(sparse_path)

        if success:
            store.update_snapshot(task_id, pct, snap_filename, "ready")
        else:
            store.update_snapshot(task_id, pct, None, "failed")
    except Exception as e:
        logger.error("snapshot_point_failed", task_id=task_id, percent=pct, error=str(e))
        store.update_snapshot(task_id, pct, None, "failed")


def _download_byte_offset(store, handle, video, task_id, sample_points,
                           timeout, head_pieces, head_bytes,
                           task_tmp, task_snap, num_pieces):
    """Fallback for non-MP4 or files without keyframe index."""
    from core.torrent_parser import compute_sample_pieces
    from core.smart_downloader import setup_piece_priorities, monitor_download

    sample_map = setup_piece_priorities(handle, video, sample_points)

    all_needed = set()
    for pieces in sample_map.values():
        all_needed.update(pieces)
    all_needed.update(head_pieces)
    store.update_task(task_id, pieces_needed=len(all_needed))

    def on_sample_ready(pct):
        try:
            pieces = sample_map[pct]
            segment_path = os.path.join(task_tmp, f"segment_{pct:02d}.bin")
            from core.segment_extractor import extract_segment
            extract_segment(handle, video, pieces, segment_path)
            segment_data = open(segment_path, "rb").read()

            # 拼接 head + segment 成稀疏 MP4 供 ffmpeg 解码
            sparse_path = os.path.join(task_tmp, f"sparse_{pct:02d}.mp4")
            with open(sparse_path, "wb") as f:
                f.write(head_bytes)
                first_piece = pieces[0]
                seg_offset = first_piece * video.piece_length - video.offset
                if seg_offset > len(head_bytes):
                    f.seek(seg_offset)
                f.write(segment_data)

            snap_filename = f"snap_{pct:02d}.jpg"
            snap_path = os.path.join(task_snap, snap_filename)
            success = generate_snapshot(sparse_path, snap_path, seek_offset=0)
            cleanup_segment(sparse_path)
            cleanup_segment(segment_path)

            if success:
                store.update_snapshot(task_id, pct, snap_filename, "ready")
            else:
                store.update_snapshot(task_id, pct, None, "failed")
        except Exception as e:
            logger.error("snapshot_failed", task_id=task_id, pct=pct, error=str(e))

    def on_progress(p):
        store.update_task(
            task_id,
            pieces_downloaded=p.pieces_downloaded,
            download_speed_bps=p.download_speed_bps,
            peers_connected=p.peers_connected,
        )

    progress = monitor_download(handle, sample_map, head_pieces,
                                timeout=timeout, on_sample_ready=on_sample_ready,
                                on_progress=on_progress,
                                is_cancelled=lambda: _is_cancelled(store, task_id))

    if progress.cancelled:
        raise TaskCancelled()
    store.update_task(task_id, status="timeout" if progress.timed_out else "completed")
