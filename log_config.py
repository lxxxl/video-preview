import os
import threading
import structlog
from config import LOG_DIR, LOG_MAX_SIZE_MB


_lock = threading.Lock()
_configured = False


def _rotate_if_needed(path, max_bytes):
    try:
        if os.path.exists(path) and os.path.getsize(path) > max_bytes:
            rotated = path + ".1"
            if os.path.exists(rotated):
                os.remove(rotated)
            os.rename(path, rotated)
    except OSError:
        pass


def _file_writer_factory():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "app.log")
    max_bytes = LOG_MAX_SIZE_MB * 1024 * 1024

    def write(event_str):
        _rotate_if_needed(log_path, max_bytes)
        with _lock:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(event_str + "\n")

    return write


_file_write = None


def _file_sink_processor(logger, method_name, event_dict):
    global _file_write
    if _file_write is None:
        _file_write = _file_writer_factory()

    rendered = structlog.processors.JSONRenderer()(logger, method_name, event_dict.copy())
    _file_write(rendered)
    return event_dict


def setup_logging():
    global _configured
    if _configured:
        return
    _configured = True

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            _file_sink_processor,
            structlog.processors.JSONRenderer(),
        ],
    )
