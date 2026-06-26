import os
import sys
import structlog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SNAPSHOT_DIR, TEMP_DIR
from storage.task_store import TaskStore
from core.torrent_parser import find_largest_video, compute_sample_pieces, compute_head_tail_pieces
from core.smart_downloader import setup_piece_priorities, monitor_download
from core.segment_extractor import extract_segment, cleanup_segment
from core.snapshot_generator import generate_snapshot
from config import SNAPSHOT_SETTINGS

logger = structlog.get_logger(__name__)


def process_task(task_id: str, magnet: str, sample_points: list[int], timeout: int):
    store = TaskStore()
    try:
        _run_task(store, task_id, magnet, sample_points, timeout)
    except Exception as e:
        logger.error("task_failed", task_id=task_id, error=str(e))
        store.update_task(task_id, status="failed", error=str(e))
    finally:
        store.close()


def _run_task(store: TaskStore, task_id: str, magnet: str, sample_points: list[int], timeout: int):
    store.update_task(task_id, status="resolving_metadata")

    from core.torrent_parser import resolve_metadata
    handle = resolve_metadata(magnet, timeout=60)

    video = find_largest_video(handle)
    store.update_task(
        task_id,
        status="downloading",
        metadata_resolved=1,
        video_file=video.name,
        video_size_bytes=video.size,
    )

    sample_map = setup_piece_priorities(handle, video, sample_points)
    head_pieces, tail_pieces = compute_head_tail_pieces(
        video, SNAPSHOT_SETTINGS["head_bytes"], SNAPSHOT_SETTINGS["tail_bytes"]
    )

    all_needed = set()
    for pieces in sample_map.values():
        all_needed.update(pieces)
    all_needed.update(head_pieces)
    all_needed.update(tail_pieces)
    store.update_task(task_id, pieces_needed=len(all_needed))

    task_tmp = os.path.join(TEMP_DIR, task_id)
    task_snap = os.path.join(SNAPSHOT_DIR, task_id)
    os.makedirs(task_tmp, exist_ok=True)
    os.makedirs(task_snap, exist_ok=True)

    def on_sample_ready(pct):
        _generate_for_point(store, handle, video, sample_map, task_id, pct, task_tmp, task_snap)

    store.update_task(task_id, status="downloading")
    progress = monitor_download(handle, sample_map, head_pieces, timeout=timeout, on_sample_ready=on_sample_ready)

    store.update_task(
        task_id,
        pieces_downloaded=progress.pieces_downloaded,
        download_speed_bps=progress.download_speed_bps,
        peers_connected=progress.peers_connected,
    )

    if progress.timed_out:
        store.update_task(task_id, status="timeout")
    else:
        store.update_task(task_id, status="completed")

    from core.session_manager import SessionManager
    SessionManager().remove_torrent(handle, delete_files=True)

    import shutil
    if os.path.isdir(task_tmp):
        shutil.rmtree(task_tmp, ignore_errors=True)


def _generate_for_point(store, handle, video, sample_map, task_id, pct, task_tmp, task_snap):
    try:
        store.update_task(task_id, status="generating")
        pieces = sample_map[pct]
        segment_path = os.path.join(task_tmp, f"segment_{pct:02d}.bin")
        extract_segment(handle, video, pieces, segment_path)

        center_piece = pieces[len(pieces) // 2]
        center_byte = center_piece * video.piece_length
        first_byte = pieces[0] * video.piece_length
        seek_offset = max(0, (center_byte - first_byte) / video.piece_length)

        snap_filename = f"snap_{pct:02d}.jpg"
        snap_path = os.path.join(task_snap, snap_filename)

        success = generate_snapshot(segment_path, snap_path, seek_offset=seek_offset)
        cleanup_segment(segment_path)

        if success:
            store.update_snapshot(task_id, pct, snap_filename, "ready")
        else:
            store.update_snapshot(task_id, pct, None, "failed")
    except Exception as e:
        logger.error("snapshot_point_failed", task_id=task_id, percent=pct, error=str(e))
        store.update_snapshot(task_id, pct, None, "failed")
