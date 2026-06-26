import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LIBTORRENT_SETTINGS = {
    "listen_port_range": (6881, 6891),
    "download_rate_limit": 5 * 1024 * 1024,
    "connections_limit": 100,
    "metadata_timeout": 60,
}

TASK_SETTINGS = {
    "max_concurrent_tasks": 3,
    "max_queue_size": 20,
    "task_timeout": 600,
    "max_disk_per_task_mb": 500,
    "global_disk_limit_gb": 10,
}

SNAPSHOT_SETTINGS = {
    "output_width": 1280,
    "output_format": "jpeg",
    "jpeg_quality": 85,
    "ffmpeg_timeout": 30,
    "adjacent_pieces": 2,
    "head_bytes": 2 * 1024 * 1024,
    "tail_bytes": 10 * 1024 * 1024,
}

CLEANUP_SETTINGS = {
    "snapshot_ttl_hours": 24,
    "cleanup_interval_minutes": 60,
    "disk_high_watermark_percent": 80,
}

RATE_LIMIT = {
    "per_ip_per_minute": 5,
    "global_per_minute": 30,
}

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
DATABASE_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "data", "tasks.db"))
SNAPSHOT_DIR = os.environ.get("SNAPSHOT_DIR", os.path.join(BASE_DIR, "data", "snapshots"))
TEMP_DIR = os.environ.get("TEMP_DIR", os.path.join(BASE_DIR, "data", "tmp"))
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", os.path.join(BASE_DIR, "data", "downloads"))
