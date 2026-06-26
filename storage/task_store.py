import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from config import DATABASE_PATH


class TaskStore:
    def __init__(self, db_path: str = None):
        self._db_path = db_path or DATABASE_PATH
        self._local = threading.local()
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path, timeout=30)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=30000")
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                info_hash TEXT NOT NULL,
                magnet TEXT NOT NULL,
                sample_points TEXT NOT NULL,
                timeout INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                video_file TEXT,
                video_size_bytes INTEGER,
                pieces_needed INTEGER DEFAULT 0,
                pieces_downloaded INTEGER DEFAULT 0,
                download_speed_bps INTEGER DEFAULT 0,
                peers_connected INTEGER DEFAULT 0,
                metadata_resolved INTEGER DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                percent INTEGER NOT NULL,
                filename TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            )
        """)
        conn.commit()

    def create_task(self, task_id: str, info_hash: str, magnet: str, sample_points: list[int], timeout: int):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO tasks (task_id, info_hash, magnet, sample_points, timeout, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
            (task_id, info_hash, magnet, json.dumps(sample_points), timeout, now, now),
        )
        for pct in sample_points:
            conn.execute(
                "INSERT INTO snapshots (task_id, percent, status) VALUES (?, ?, 'pending')",
                (task_id, pct),
            )
        conn.commit()

    def get_task_status(self, task_id: str) -> str | None:
        conn = self._get_conn()
        row = conn.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return row["status"] if row else None

    def get_task(self, task_id: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None

        task = dict(row)
        task["sample_points"] = json.loads(task["sample_points"])
        task["metadata_resolved"] = bool(task["metadata_resolved"])

        info_hash = task.get("info_hash", task_id)

        snapshots = conn.execute(
            "SELECT percent, filename, status FROM snapshots WHERE task_id = ? ORDER BY percent",
            (task_id,),
        ).fetchall()

        task["snapshots"] = []
        for s in snapshots:
            snap = dict(s)
            if snap["filename"]:
                snap["url"] = f"/snapshots/{info_hash}/{snap['filename']}"
            else:
                snap["url"] = None
            task["snapshots"].append(snap)

        return task

    def update_task(self, task_id: str, **kwargs):
        if not kwargs:
            return
        now = datetime.now(timezone.utc).isoformat()
        kwargs["updated_at"] = now
        cols = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [task_id]
        conn = self._get_conn()
        conn.execute(f"UPDATE tasks SET {cols} WHERE task_id = ?", vals)
        conn.commit()

    def update_snapshot(self, task_id: str, percent: int, filename: str, status: str):
        conn = self._get_conn()
        conn.execute(
            "UPDATE snapshots SET filename = ?, status = ? WHERE task_id = ? AND percent = ?",
            (filename, status, task_id, percent),
        )
        conn.commit()

    def has_cached_snapshots(self, info_hash: str, sample_points: list[int]) -> bool:
        from config import SNAPSHOT_DIR
        snap_dir = os.path.join(SNAPSHOT_DIR, info_hash)
        if not os.path.isdir(snap_dir):
            return False
        for pct in sample_points:
            if not os.path.isfile(os.path.join(snap_dir, f"snap_{pct:02d}.jpg")):
                return False
        return True

    def fill_from_cache(self, task_id: str, info_hash: str, sample_points: list[int]):
        conn = self._get_conn()
        for pct in sample_points:
            filename = f"snap_{pct:02d}.jpg"
            conn.execute(
                "UPDATE snapshots SET filename = ?, status = 'ready' WHERE task_id = ? AND percent = ?",
                (filename, task_id, pct),
            )
        conn.execute(
            "UPDATE tasks SET status = 'completed', updated_at = ? WHERE task_id = ?",
            (datetime.now(timezone.utc).isoformat(), task_id),
        )
        conn.commit()

    def get_expired_tasks(self, ttl_hours: int) -> list[str]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT task_id FROM tasks WHERE status IN ('completed', 'failed', 'timeout', 'cancelled') "
            "AND updated_at < datetime('now', ?)",
            (f"-{ttl_hours} hours",),
        ).fetchall()
        return [r["task_id"] for r in rows]

    def count_tasks_by_info_hash(self, info_hash: str) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE info_hash = ?", (info_hash,)
        ).fetchone()
        return row["cnt"] if row else 0

    def delete_task(self, task_id: str):
        conn = self._get_conn()
        conn.execute("DELETE FROM snapshots WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        conn.commit()

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
