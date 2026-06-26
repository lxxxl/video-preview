import time
import libtorrent as lt
import structlog
from dataclasses import dataclass, field
from config import TASK_SETTINGS, SNAPSHOT_SETTINGS
from core.torrent_parser import (
    VideoFileInfo,
    compute_sample_pieces,
    compute_head_tail_pieces,
)

logger = structlog.get_logger(__name__)


@dataclass
class DownloadProgress:
    pieces_needed: int = 0
    pieces_downloaded: int = 0
    download_speed_bps: int = 0
    peers_connected: int = 0
    ready_sample_points: list[int] = field(default_factory=list)
    timed_out: bool = False


def setup_piece_priorities(
    handle: lt.torrent_handle,
    video: VideoFileInfo,
    sample_points: list[int],
) -> dict[int, list[int]]:
    num_pieces = video.total_pieces
    handle.prioritize_pieces([0] * num_pieces)

    sample_map = compute_sample_pieces(
        video, sample_points, SNAPSHOT_SETTINGS["adjacent_pieces"]
    )
    head_pieces, tail_pieces = compute_head_tail_pieces(
        video, SNAPSHOT_SETTINGS["head_bytes"], SNAPSHOT_SETTINGS["tail_bytes"]
    )

    priorities = [0] * num_pieces

    for pieces in sample_map.values():
        for p in pieces:
            if 0 <= p < num_pieces:
                priorities[p] = max(priorities[p], 6)

    for p in tail_pieces:
        if 0 <= p < num_pieces:
            priorities[p] = max(priorities[p], 7)

    for p in head_pieces:
        if 0 <= p < num_pieces:
            priorities[p] = max(priorities[p], 7)

    handle.prioritize_pieces(priorities)

    deadline_ms = 0
    for p in head_pieces:
        if 0 <= p < num_pieces:
            handle.set_piece_deadline(p, deadline_ms, lt.deadline_flags_t.alert_when_available)
            deadline_ms += 100

    for p in tail_pieces:
        if 0 <= p < num_pieces:
            handle.set_piece_deadline(p, deadline_ms, lt.deadline_flags_t.alert_when_available)
            deadline_ms += 100

    for pct in sorted(sample_map.keys()):
        for p in sample_map[pct]:
            if 0 <= p < num_pieces:
                handle.set_piece_deadline(p, deadline_ms, lt.deadline_flags_t.alert_when_available)
                deadline_ms += 100

    handle.unset_flags(lt.torrent_flags.upload_mode)
    handle.resume()

    all_needed = set()
    for pieces in sample_map.values():
        all_needed.update(pieces)
    all_needed.update(head_pieces)
    all_needed.update(tail_pieces)

    logger.info(
        "priorities_set",
        total_needed=len(all_needed),
        sample_points=len(sample_map),
    )

    return sample_map


def monitor_download(
    handle: lt.torrent_handle,
    sample_map: dict[int, list[int]],
    head_pieces: list[int],
    timeout: int = None,
    on_sample_ready=None,
) -> DownloadProgress:
    if timeout is None:
        timeout = TASK_SETTINGS["task_timeout"]

    all_needed = set()
    for pieces in sample_map.values():
        all_needed.update(pieces)
    all_needed.update(head_pieces)

    progress = DownloadProgress(pieces_needed=len(all_needed))
    notified = set()
    deadline = time.time() + timeout

    head_set = set(head_pieces)
    last_log = 0

    while time.time() < deadline:
        downloaded = sum(1 for p in all_needed if handle.have_piece(p))
        status = handle.status()
        progress.pieces_downloaded = downloaded
        progress.download_speed_bps = int(status.download_rate)
        progress.peers_connected = status.num_peers

        now = time.time()
        if now - last_log >= 10:
            state_names = ["queued", "checking", "metadata", "downloading",
                           "finished", "seeding", "allocating", "checking_resume"]
            state_str = state_names[status.state] if status.state < len(state_names) else str(status.state)
            logger.info(
                "download_progress",
                pieces=f"{downloaded}/{len(all_needed)}",
                speed_kbps=round(status.download_rate / 1024, 1),
                peers=status.num_peers,
                seeds=status.num_seeds,
                state=state_str,
                progress_pct=round(status.progress * 100, 2),
            )
            last_log = now

        head_ready = all(handle.have_piece(p) for p in head_set)

        if head_ready:
            for pct, pieces in sample_map.items():
                if pct in notified:
                    continue
                if all(handle.have_piece(p) for p in pieces):
                    notified.add(pct)
                    progress.ready_sample_points.append(pct)
                    logger.info("sample_point_ready", percent=pct)
                    if on_sample_ready:
                        on_sample_ready(pct)

        if len(notified) == len(sample_map):
            break

        time.sleep(1)

    if time.time() >= deadline:
        progress.timed_out = True
        logger.warning("download_timeout", downloaded=progress.pieces_downloaded)

    return progress
