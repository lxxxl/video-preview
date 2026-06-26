import os
import time
import libtorrent as lt
import structlog
from core.torrent_parser import VideoFileInfo
from core.session_manager import SessionManager

logger = structlog.get_logger(__name__)


def read_pieces(handle, pieces: list[int], timeout: float = 30.0) -> dict[int, bytes]:
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

    return piece_data


def assemble_segment(
    piece_data: dict[int, bytes],
    video: VideoFileInfo,
    output_path: str,
) -> str:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

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

    return output_path


def extract_segment(
    handle,
    video: VideoFileInfo,
    pieces: list[int],
    output_path: str,
    timeout: float = 30.0,
) -> str:
    piece_data = read_pieces(handle, pieces, timeout)
    if not piece_data:
        raise RuntimeError("No piece data could be read")

    assemble_segment(piece_data, video, output_path)

    logger.info(
        "segment_extracted",
        pieces=len(piece_data),
        output=output_path,
        size_kb=round(os.path.getsize(output_path) / 1024, 1),
    )
    return output_path


def extract_annexb_segment(
    handle,
    video: VideoFileInfo,
    head_pieces: list[int],
    sample_pieces: list[int],
    output_path: str,
    timeout: float = 30.0,
) -> tuple[str, str | None]:
    all_pieces = list(set(head_pieces + sample_pieces))
    piece_data = read_pieces(handle, all_pieces, timeout)

    head_path = output_path + ".head"
    assemble_segment(
        {p: piece_data[p] for p in sorted(head_pieces) if p in piece_data},
        video,
        head_path,
    )

    from core.mp4_utils import extract_codec_config, avc_to_annexb

    head_data = open(head_path, "rb").read()
    config = extract_codec_config(head_data)

    if config and config["codec"] == "h264" and config["sps"]:
        sample_data_dict = {p: piece_data[p] for p in sorted(sample_pieces) if p in piece_data}
        raw_segment = b""
        for p in sorted(sample_data_dict.keys()):
            data = sample_data_dict[p]
            start_byte = p * video.piece_length
            end_byte = start_byte + len(data)
            trim_start = max(0, video.offset - start_byte)
            trim_end = max(0, end_byte - (video.offset + video.size))
            if trim_end > 0:
                data = data[trim_start:-trim_end]
            else:
                data = data[trim_start:]
            raw_segment += data

        annexb_data = avc_to_annexb(
            raw_segment,
            config["nalu_length_size"],
            config["sps"],
            config["pps"],
        )

        with open(output_path, "wb") as f:
            f.write(annexb_data)

        logger.info(
            "annexb_segment_extracted",
            pieces=len(sample_pieces),
            codec="h264",
            output=output_path,
            size_kb=round(os.path.getsize(output_path) / 1024, 1),
        )
        os.remove(head_path)
        return output_path, "h264"

    os.remove(head_path)
    assemble_segment(
        {p: piece_data[p] for p in sorted(sample_pieces) if p in piece_data},
        video,
        output_path,
    )
    logger.info(
        "segment_extracted",
        pieces=len(sample_pieces),
        output=output_path,
        size_kb=round(os.path.getsize(output_path) / 1024, 1),
    )
    return output_path, None


def cleanup_segment(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.warning("cleanup_failed", path=path, error=str(e))
