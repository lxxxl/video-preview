"""API benchmark tests — test full request/response cycle and cache performance."""
import os
import time
import pytest


MAGNET = "magnet:?xt=urn:btih:aabbccddee11223344556677889900aabbccddee"
INFO_HASH = "aabbccddee11223344556677889900aabbccddee"
SAMPLE_POINTS = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95]


class TestAPIBenchmark:
    def _populate_cache(self, tmp_dirs):
        import config
        snap_dir = os.path.join(config.SNAPSHOT_DIR, INFO_HASH)
        os.makedirs(snap_dir, exist_ok=True)
        for pct in SAMPLE_POINTS:
            with open(os.path.join(snap_dir, f"snap_{pct:02d}.jpg"), "wb") as f:
                f.write(b"\xff\xd8\xff" + b"\x00" * 5000)

    def test_create_task_latency(self, client):
        t0 = time.perf_counter()
        for _ in range(100):
            client.post("/api/task", json={"magnet": MAGNET})
        elapsed = time.perf_counter() - t0
        avg = elapsed / 100 * 1000
        print(f"\n  POST /api/task (no cache): {avg:.2f}ms avg ({elapsed:.2f}s total for 100 requests)")
        assert avg < 50  # should be < 50ms per request

    def test_cache_hit_latency(self, client, tmp_dirs):
        self._populate_cache(tmp_dirs)
        t0 = time.perf_counter()
        for _ in range(100):
            resp = client.post("/api/task", json={"magnet": MAGNET})
            assert resp.status_code == 200
        elapsed = time.perf_counter() - t0
        avg = elapsed / 100 * 1000
        print(f"\n  POST /api/task (cache hit): {avg:.2f}ms avg ({elapsed:.2f}s total for 100 requests)")
        assert avg < 50

    def test_get_task_latency(self, client):
        resp = client.post("/api/task", json={"magnet": MAGNET})
        task_id = resp.get_json()["task_id"]
        t0 = time.perf_counter()
        for _ in range(100):
            client.get(f"/api/task/{task_id}")
        elapsed = time.perf_counter() - t0
        avg = elapsed / 100 * 1000
        print(f"\n  GET /api/task/<id>: {avg:.2f}ms avg ({elapsed:.2f}s total for 100 requests)")
        assert avg < 50

    def test_get_snapshots_latency(self, client):
        resp = client.post("/api/task", json={"magnet": MAGNET})
        task_id = resp.get_json()["task_id"]
        t0 = time.perf_counter()
        for _ in range(100):
            client.get(f"/api/task/{task_id}/snapshots")
        elapsed = time.perf_counter() - t0
        avg = elapsed / 100 * 1000
        print(f"\n  GET /api/task/<id>/snapshots: {avg:.2f}ms avg ({elapsed:.2f}s total for 100 requests)")
        assert avg < 50

    def test_serve_snapshot_latency(self, client, tmp_dirs):
        self._populate_cache(tmp_dirs)
        t0 = time.perf_counter()
        for _ in range(100):
            client.get(f"/snapshots/{INFO_HASH}/snap_50.jpg")
        elapsed = time.perf_counter() - t0
        avg = elapsed / 100 * 1000
        print(f"\n  GET /snapshots/<hash>/snap.jpg: {avg:.2f}ms avg ({elapsed:.2f}s total for 100 requests)")
        assert avg < 50

    def test_cache_hit_response_structure(self, client, tmp_dirs):
        self._populate_cache(tmp_dirs)
        resp = client.post("/api/task", json={"magnet": MAGNET})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "completed"
        assert data["message"] == "Cache hit"

        task_id = data["task_id"]
        resp2 = client.get(f"/api/task/{task_id}")
        task = resp2.get_json()
        assert task["status"] == "completed"
        assert task["info_hash"] == INFO_HASH
        assert len(task["snapshots"]) == len(SAMPLE_POINTS)
        for snap in task["snapshots"]:
            assert snap["status"] == "ready"
            assert snap["url"].startswith(f"/snapshots/{INFO_HASH}/")

    def test_multiple_tasks_same_magnet_share_cache(self, client, tmp_dirs):
        self._populate_cache(tmp_dirs)

        task_ids = []
        for _ in range(5):
            resp = client.post("/api/task", json={"magnet": MAGNET})
            assert resp.status_code == 200
            task_ids.append(resp.get_json()["task_id"])

        for tid in task_ids:
            resp = client.get(f"/api/task/{tid}")
            task = resp.get_json()
            assert task["status"] == "completed"
            assert task["info_hash"] == INFO_HASH
            for snap in task["snapshots"]:
                assert INFO_HASH in snap["url"]

    def test_different_magnets_no_cache_collision(self, client, tmp_dirs):
        magnet2 = "magnet:?xt=urn:btih:1122334455667788990011223344556677889900"
        self._populate_cache(tmp_dirs)

        resp1 = client.post("/api/task", json={"magnet": MAGNET})
        assert resp1.status_code == 200
        assert resp1.get_json()["status"] == "completed"

        resp2 = client.post("/api/task", json={"magnet": magnet2})
        assert resp2.status_code == 201
        assert resp2.get_json()["status"] == "queued"
