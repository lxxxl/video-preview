import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LIBTORRENT_SETTINGS = {
    "listen_port_range": (6881, 6891),
    "download_rate_limit": 10 * 1024 * 1024,   # 10 MB/s (原 5)
    "upload_rate_limit": 200 * 1024,            # 200 KB/s upload — BT 互惠：只下不传会被限速
    "upload_slots_limit": 8,                     # 上传槽位数
    "connections_limit": 300,                    # 增加连接数 (原 200)
    "metadata_timeout": 180,
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

# ── 方案 D: 冷门/大文件自适应降级 ──
# 视频超过阈值时自动减少采样点 + 降低分辨率，提升 600s 窗口内完成率
ADAPTIVE_DOWNGRADE = {
    "enabled": True,
    "tiers": [
        # (字节阈值, 采样点, adjacent_pieces, output_width)
        # 从大到小，找到第一个 >= 阈值的
        (2 * 1024**3, [5, 15, 25, 35, 45, 55, 65, 75, 85, 95], 1, 854),   # >2GB: 10点, 480P
        (1 * 1024**3, [5, 10, 15, 20, 25, 30, 35, 40, 45, 50,
                        55, 60, 65, 70, 75, 80, 85, 90, 95], 2, 1280),     # >1GB: 19点, 720P
    ],
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

LOG_DIR = os.environ.get("LOG_DIR", os.path.join(BASE_DIR, "data", "logs"))
LOG_MAX_SIZE_MB = 10

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
DATABASE_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "data", "tasks.db"))
SNAPSHOT_DIR = os.environ.get("SNAPSHOT_DIR", os.path.join(BASE_DIR, "data", "snapshots"))
TEMP_DIR = os.environ.get("TEMP_DIR", os.path.join(BASE_DIR, "data", "tmp"))
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", os.path.join(BASE_DIR, "data", "downloads"))
