import json
import os
import pytest

MAGNET = "magnet:?xt=urn:btih:aabbccddee11223344556677889900aabbccddee"
INFO_HASH = "aabbccddee11223344556677889900aabbccddee"


class TestCreateTask:
    def test_valid_request(self, client):
        resp = client.post("/api/task", json={"magnet": MAGNET})
        assert resp.status_code == 201
        data = resp.get_json()
        assert "task_id" in data
        assert data["status"] == "queued"

    def test_with_custom_sample_points(self, client):
        resp = client.post(
            "/api/task",
            json={"magnet": MAGNET, "sample_points": [25, 50, 75], "timeout": 300},
        )
        assert resp.status_code == 201

    def test_invalid_magnet(self, client):
        resp = client.post("/api/task", json={"magnet": "not-a-magnet"})
        assert resp.status_code == 400

    def test_missing_magnet(self, client):
        resp = client.post("/api/task", json={})
        assert resp.status_code == 400

    def test_empty_body(self, client):
        resp = client.post("/api/task", content_type="application/json")
        assert resp.status_code == 400

    def test_invalid_sample_points(self, client):
        resp = client.post("/api/task", json={"magnet": MAGNET, "sample_points": [0, 100]})
        assert resp.status_code == 400

    def test_too_many_sample_points(self, client):
        resp = client.post("/api/task", json={"magnet": MAGNET, "sample_points": list(range(1, 25))})
        assert resp.status_code == 400

    def test_invalid_timeout(self, client):
        resp = client.post("/api/task", json={"magnet": MAGNET, "timeout": 10})
        assert resp.status_code == 400


class TestCacheHit:
    def test_cache_hit_returns_completed(self, client, tmp_dirs):
        import config
        snap_dir = os.path.join(config.SNAPSHOT_DIR, INFO_HASH)
        os.makedirs(snap_dir, exist_ok=True)
        from api.schemas import DEFAULT_SAMPLE_POINTS
        for pct in DEFAULT_SAMPLE_POINTS:
            with open(os.path.join(snap_dir, f"snap_{pct:02d}.jpg"), "wb") as f:
                f.write(b"\xff\xd8\xff" + b"\x00" * 100)

        resp = client.post("/api/task", json={"magnet": MAGNET})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "completed"
        assert data["message"] == "Cache hit"

        task_id = data["task_id"]
        resp2 = client.get(f"/api/task/{task_id}")
        task = resp2.get_json()
        assert task["status"] == "completed"
        ready_snaps = [s for s in task["snapshots"] if s["status"] == "ready"]
        assert len(ready_snaps) == len(DEFAULT_SAMPLE_POINTS)

    def test_no_cache_queues_task(self, client):
        resp = client.post("/api/task", json={"magnet": MAGNET})
        assert resp.status_code == 201
        assert resp.get_json()["status"] == "queued"

    def test_partial_cache_queues_task(self, client, tmp_dirs):
        import config
        snap_dir = os.path.join(config.SNAPSHOT_DIR, INFO_HASH)
        os.makedirs(snap_dir, exist_ok=True)
        with open(os.path.join(snap_dir, "snap_05.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff")

        resp = client.post("/api/task", json={"magnet": MAGNET})
        assert resp.status_code == 201
        assert resp.get_json()["status"] == "queued"


class TestGetTask:
    def test_existing_task(self, client):
        resp = client.post("/api/task", json={"magnet": MAGNET})
        task_id = resp.get_json()["task_id"]

        resp = client.get(f"/api/task/{task_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["task_id"] == task_id
        assert data["info_hash"] == INFO_HASH
        assert "snapshots" in data

    def test_nonexistent_task(self, client):
        resp = client.get("/api/task/doesnotexist")
        assert resp.status_code == 404


class TestGetSnapshots:
    def test_empty_snapshots(self, client):
        resp = client.post("/api/task", json={"magnet": MAGNET})
        task_id = resp.get_json()["task_id"]

        resp = client.get(f"/api/task/{task_id}/snapshots")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["snapshots"] == []

    def test_nonexistent_task(self, client):
        resp = client.get("/api/task/nope/snapshots")
        assert resp.status_code == 404


class TestServeSnapshot:
    def test_serve_from_info_hash_dir(self, client, tmp_dirs):
        import config
        snap_dir = os.path.join(config.SNAPSHOT_DIR, INFO_HASH)
        os.makedirs(snap_dir, exist_ok=True)
        with open(os.path.join(snap_dir, "snap_50.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff" + b"\x00" * 100)

        resp = client.get(f"/snapshots/{INFO_HASH}/snap_50.jpg")
        assert resp.status_code == 200

    def test_invalid_filename(self, client, tmp_dirs):
        import config
        snap_dir = os.path.join(config.SNAPSHOT_DIR, INFO_HASH)
        os.makedirs(snap_dir, exist_ok=True)
        resp = client.get(f"/snapshots/{INFO_HASH}/bad.png")
        assert resp.status_code == 400


class TestDeleteTask:
    def test_delete_existing(self, client):
        resp = client.post("/api/task", json={"magnet": MAGNET})
        task_id = resp.get_json()["task_id"]

        resp = client.delete(f"/api/task/{task_id}")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "cancelled"

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/task/nope")
        assert resp.status_code == 404


class TestTaskStore:
    def test_create_and_get(self, task_store):
        task_store.create_task("t1", "abc123", "magnet:test", [25, 50, 75], 600)
        task = task_store.get_task("t1")
        assert task is not None
        assert task["task_id"] == "t1"
        assert task["info_hash"] == "abc123"
        assert task["status"] == "queued"
        assert len(task["snapshots"]) == 3

    def test_update_task(self, task_store):
        task_store.create_task("t2", "abc123", "magnet:test", [50], 600)
        task_store.update_task("t2", status="downloading", video_file="test.mp4")
        task = task_store.get_task("t2")
        assert task["status"] == "downloading"
        assert task["video_file"] == "test.mp4"

    def test_update_snapshot(self, task_store):
        task_store.create_task("t3", "hash3", "magnet:test", [50], 600)
        task_store.update_snapshot("t3", 50, "snap_50.jpg", "ready")
        task = task_store.get_task("t3")
        snap = task["snapshots"][0]
        assert snap["filename"] == "snap_50.jpg"
        assert snap["status"] == "ready"
        assert snap["url"] == "/snapshots/hash3/snap_50.jpg"

    def test_get_nonexistent(self, task_store):
        assert task_store.get_task("nope") is None

    def test_delete_task(self, task_store):
        task_store.create_task("t4", "hash4", "magnet:test", [50], 600)
        task_store.delete_task("t4")
        assert task_store.get_task("t4") is None

    def test_has_cached_snapshots(self, task_store, tmp_dirs):
        import config
        snap_dir = os.path.join(config.SNAPSHOT_DIR, "testhash")
        os.makedirs(snap_dir, exist_ok=True)
        for pct in [25, 50, 75]:
            with open(os.path.join(snap_dir, f"snap_{pct:02d}.jpg"), "wb") as f:
                f.write(b"data")
        assert task_store.has_cached_snapshots("testhash", [25, 50, 75]) is True
        assert task_store.has_cached_snapshots("testhash", [25, 50, 90]) is False
        assert task_store.has_cached_snapshots("nohash", [25]) is False

    def test_fill_from_cache(self, task_store):
        task_store.create_task("t5", "hash5", "magnet:test", [25, 50], 600)
        task_store.fill_from_cache("t5", "hash5", [25, 50])
        task = task_store.get_task("t5")
        assert task["status"] == "completed"
        assert all(s["status"] == "ready" for s in task["snapshots"])
        assert all(s["filename"] is not None for s in task["snapshots"])

    def test_count_tasks_by_info_hash(self, task_store):
        task_store.create_task("t6", "shared", "magnet:test", [50], 600)
        task_store.create_task("t7", "shared", "magnet:test", [50], 600)
        assert task_store.count_tasks_by_info_hash("shared") == 2
        task_store.delete_task("t6")
        assert task_store.count_tasks_by_info_hash("shared") == 1
