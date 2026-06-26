import re
import time
import libtorrent as lt
import structlog
from dataclasses import dataclass
from config import LIBTORRENT_SETTINGS
from core.session_manager import SessionManager

logger = structlog.get_logger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".flv", ".ts"}

MAGNET_PATTERN = re.compile(
    r"^magnet:\?xt=urn:btih:([0-9a-fA-F]{40}|[A-Za-z2-7]{32})(&.*)?$"
)


@dataclass
class VideoFileInfo:
    name: str
    size: int
    file_index: int
    offset: int
    first_piece: int
    last_piece: int
    piece_length: int
    total_pieces: int


def validate_magnet(magnet_uri: str) -> bool:
    return bool(MAGNET_PATTERN.match(magnet_uri))


def extract_info_hash(magnet_uri: str) -> str:
    m = MAGNET_PATTERN.match(magnet_uri)
    if not m:
        raise ValueError("Invalid magnet URI")
    return m.group(1).lower()


def resolve_metadata(magnet_uri: str, timeout: int = None) -> lt.torrent_handle:
    if timeout is None:
        timeout = LIBTORRENT_SETTINGS["metadata_timeout"]

    if not validate_magnet(magnet_uri):
        raise ValueError("Invalid magnet URI format")

    sm = SessionManager()
    params = lt.parse_magnet_uri(magnet_uri)
    params.flags |= lt.torrent_flags.upload_mode
    handle = sm.add_torrent(params)

    deadline = time.time() + timeout
    while not handle.has_metadata():
        if time.time() > deadline:
            sm.remove_torrent(handle)
            raise TimeoutError(
                f"Metadata resolution timed out after {timeout}s"
            )
        time.sleep(0.5)

    info_hash_short = str(handle.info_hash())[:8]
    logger.info("metadata_resolved", info_hash=info_hash_short)
    return handle


def find_largest_video(handle: lt.torrent_handle) -> VideoFileInfo:
    torrent_info = handle.torrent_file()
    if torrent_info is None:
        raise RuntimeError("Torrent metadata not available")

    file_storage = torrent_info.files()
    piece_length = torrent_info.piece_length()
    total_pieces = torrent_info.num_pieces()

    best = None
    for i in range(file_storage.num_files()):
        path = file_storage.file_path(i)
        ext = _get_extension(path)
        if ext not in VIDEO_EXTENSIONS:
            continue

        size = file_storage.file_size(i)
        if best is None or size > best["size"]:
            best = {"index": i, "name": path, "size": size}

    if best is None:
        raise ValueError("No video files found in torrent")

    offset = file_storage.file_offset(best["index"])
    first_piece = offset // piece_length
    last_piece = (offset + best["size"] - 1) // piece_length

    logger.info(
        "video_identified",
        file=best["name"],
        size_mb=round(best["size"] / 1024 / 1024, 1),
        pieces=f"{first_piece}-{last_piece}",
    )

    return VideoFileInfo(
        name=best["name"],
        size=best["size"],
        file_index=best["index"],
        offset=offset,
        first_piece=first_piece,
        last_piece=last_piece,
        piece_length=piece_length,
        total_pieces=total_pieces,
    )


def compute_sample_pieces(
    video: VideoFileInfo, sample_points: list[int], adjacent: int = 2
) -> dict[int, list[int]]:
    result = {}
    for pct in sample_points:
        if not 1 <= pct <= 99:
            continue
        byte_offset = video.offset + int(video.size * pct / 100)
        center_piece = byte_offset // video.piece_length

        pieces = []
        for delta in range(-adjacent, adjacent + 1):
            p = center_piece + delta
            if video.first_piece <= p <= video.last_piece:
                pieces.append(p)
        result[pct] = pieces

    return result


def compute_head_tail_pieces(
    video: VideoFileInfo, head_bytes: int, tail_bytes: int
) -> tuple[list[int], list[int]]:
    head_end = video.offset + min(head_bytes, video.size)
    head_last_piece = (head_end - 1) // video.piece_length
    head_pieces = list(range(video.first_piece, head_last_piece + 1))

    tail_start = video.offset + max(0, video.size - tail_bytes)
    tail_first_piece = tail_start // video.piece_length
    tail_pieces = list(range(tail_first_piece, video.last_piece + 1))

    return head_pieces, tail_pieces


def _get_extension(path: str) -> str:
    dot = path.rfind(".")
    if dot == -1:
        return ""
    return path[dot:].lower()
