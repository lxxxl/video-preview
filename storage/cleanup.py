import os
import shutil
import structlog
from config import SNAPSHOT_DIR, TEMP_DIR, CLEANUP_SETTINGS
from storage.task_store import TaskStore

logger = structlog.get_logger(__name__)


def cleanup_expired_tasks(store: TaskStore):
    ttl = CLEANUP_SETTINGS["snapshot_ttl_hours"]
    expired = store.get_expired_tasks(ttl)

    for task_id in expired:
        task = store.get_task(task_id)
        info_hash = task["info_hash"] if task else None
        store.delete_task(task_id)
        if info_hash and store.count_tasks_by_info_hash(info_hash) == 0:
            snap_dir = os.path.join(SNAPSHOT_DIR, info_hash)
            if os.path.isdir(snap_dir):
                shutil.rmtree(snap_dir, ignore_errors=True)
        logger.info("task_cleaned", task_id=task_id)

    return len(expired)


def cleanup_orphan_temp_files():
    if not os.path.isdir(TEMP_DIR):
        return 0

    count = 0
    for entry in os.scandir(TEMP_DIR):
        if entry.is_dir():
            shutil.rmtree(entry.path, ignore_errors=True)
            count += 1
    return count


def get_disk_usage_bytes(path: str) -> int:
    total = 0
    if not os.path.isdir(path):
        return 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def check_disk_watermark() -> tuple[int, int, bool]:
    from config import TASK_SETTINGS
    limit_bytes = TASK_SETTINGS["global_disk_limit_gb"] * 1024 * 1024 * 1024
    used = get_disk_usage_bytes(SNAPSHOT_DIR) + get_disk_usage_bytes(TEMP_DIR)
    threshold = limit_bytes * CLEANUP_SETTINGS["disk_high_watermark_percent"] / 100
    return used, limit_bytes, used > threshold
