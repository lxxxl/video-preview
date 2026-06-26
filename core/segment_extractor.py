import os
import time
import libtorrent as lt
import structlog
from core.torrent_parser import VideoFileInfo
from core.session_manager import SessionManager

logger = structlog.get_logger(__name__)


def extract_segment(
    handle: lt.torrent_handle,
    video: VideoFileInfo,
    pieces: list[int],
    output_path: str,
    timeout: float = 30.0,
) -> str:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    sm = SessionManager()

    piece_data = {}
    pending = set()

    for p in pieces:
        handle.read_piece(p)
        pending.add(p)

    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        alerts = sm.pop_alerts()
        for alert in alerts:
            if hasattr(alert, "piece") and hasattr(alert, "buffer"):
                idx = alert.piece
                if idx in pending:
                    if alert.ec.value() != 0:
                        logger.error("read_piece_failed", piece=idx, error=str(alert.ec))
                        pending.discard(idx)
                        continue
                    piece_data[idx] = alert.buffer
                    pending.discard(idx)
        if pending:
            time.sleep(0.1)

    if pending:
        logger.warning("read_piece_timeout", missing=list(pending))

    sorted_pieces = sorted(piece_data.keys())
    if not sorted_pieces:
        raise RuntimeError("No piece data could be read")

    with open(output_path, "wb") as f:
        for p in sorted_pieces:
            data = piece_data[p]
            start_byte = p * video.piece_length
            end_byte = start_byte + len(data)

            file_start = video.offset
            file_end = video.offset + video.size

            trim_start = max(0, file_start - start_byte)
            trim_end = max(0, end_byte - file_end)

            if trim_end > 0:
                data = data[trim_start:-trim_end]
            else:
                data = data[trim_start:]

            f.write(data)

    logger.info(
        "segment_extracted",
        pieces=len(sorted_pieces),
        output=output_path,
        size_kb=round(os.path.getsize(output_path) / 1024, 1),
    )
    return output_path


def cleanup_segment(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.warning("cleanup_failed", path=path, error=str(e))
