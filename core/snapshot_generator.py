import os
import subprocess
import structlog
from config import SNAPSHOT_SETTINGS

logger = structlog.get_logger(__name__)


def generate_snapshot(
    segment_path: str,
    output_path: str,
    seek_offset: float = 0.0,
    width: int = None,
    quality: int = None,
    timeout: int = None,
    codec_hint: str = None,
) -> bool:
    if width is None:
        width = SNAPSHOT_SETTINGS["output_width"]
    if quality is None:
        quality = SNAPSHOT_SETTINGS["jpeg_quality"]
    if timeout is None:
        timeout = SNAPSHOT_SETTINGS["ffmpeg_timeout"]

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    strategies = [
        _strategy_input_seek,
        _strategy_output_seek,
        _strategy_extended_probe,
        _strategy_raw_h264,     # raw codec fallback for non-container segments
        _strategy_first_frame,
    ]

    if codec_hint:
        strategies.insert(0, lambda s, o, off, w, q, t: _strategy_raw_codec(s, o, off, w, q, t, codec_hint))
        strategies.insert(1, lambda s, o, off, w, q, t: _strategy_raw_codec_first(s, o, w, q, t, codec_hint))

    for strategy in strategies:
        success = strategy(
            segment_path, output_path, seek_offset, width, quality, timeout
        )
        if success and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.info(
                "snapshot_generated",
                strategy=getattr(strategy, "__name__", str(strategy)),
                output=output_path,
            )
            return True

    logger.error("snapshot_failed", segment=segment_path)
    return False


def _strategy_raw_codec(segment, output, offset, width, quality, timeout, codec):
    cmd = [
        "ffmpeg", "-y",
        "-f", codec,
        "-i", segment,
        "-ss", str(offset),
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-q:v", str(_jpeg_q(quality)),
        output,
    ]
    return _run_ffmpeg(cmd, timeout)


def _strategy_raw_codec_first(segment, output, width, quality, timeout, codec):
    cmd = [
        "ffmpeg", "-y",
        "-f", codec,
        "-i", segment,
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-q:v", str(_jpeg_q(quality)),
        output,
    ]
    return _run_ffmpeg(cmd, timeout)


def _strategy_input_seek(segment, output, offset, width, quality, timeout):
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(offset),
        "-i", segment,
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-q:v", str(_jpeg_q(quality)),
        output,
    ]
    return _run_ffmpeg(cmd, timeout)


def _strategy_output_seek(segment, output, offset, width, quality, timeout):
    cmd = [
        "ffmpeg", "-y",
        "-i", segment,
        "-ss", str(offset),
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-q:v", str(_jpeg_q(quality)),
        output,
    ]
    return _run_ffmpeg(cmd, timeout)


def _strategy_extended_probe(segment, output, offset, width, quality, timeout):
    # Also try explicitly allowing missing moov for fragmented/streaming MP4
    cmd1 = [
        "ffmpeg", "-y",
        "-analyzeduration", "100M",
        "-probesize", "100M",
        "-ss", str(offset),
        "-i", segment,
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-q:v", str(_jpeg_q(quality)),
        output,
    ]
    return _run_ffmpeg(cmd1, timeout)


def _strategy_first_frame(segment, output, offset, width, quality, timeout):
    cmd = [
        "ffmpeg", "-y",
        "-i", segment,
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-q:v", str(_jpeg_q(quality)),
        output,
    ]
    return _run_ffmpeg(cmd, timeout)


def _strategy_raw_h264(segment, output, offset, width, quality, timeout):
    """Try raw H.264 decoding as last resort for non-container data."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "h264",
        "-i", segment,
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-q:v", str(_jpeg_q(quality)),
        output,
    ]
    return _run_ffmpeg(cmd, timeout)


def _run_ffmpeg(cmd: list[str], timeout: int) -> bool:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            # Log ffmpeg error for debugging
            stderr = result.stderr.decode("utf-8", errors="replace")[-500:] if result.stderr else ""
            if stderr:
                logger.warning("ffmpeg_error", stderr=stderr)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg_timeout", cmd=" ".join(cmd[:6]))
        return False
    except FileNotFoundError:
        logger.error("ffmpeg_not_found")
        return False


def _jpeg_q(quality: int) -> int:
    return max(1, min(31, 31 - int(quality * 30 / 100)))
