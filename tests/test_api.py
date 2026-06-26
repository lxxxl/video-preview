import json
import os
import pytest


class TestCreateTask:
    def test_valid_request(self, client):
        resp = client.post(
            "/api/task",
            json={
                "magnet": "magnet:?xt=urn:btih:aabbccddee11223344556677889900aabbccddee",
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "task_id" in data
        assert data["status"] == "queued"

    def test_with_custom_sample_points(self, client):
        resp = client.post(
            "/api/task",
            json={
                "magnet": "magnet:?xt=urn:btih:aabbccddee11223344556677889900aabbccddee",
                "sample_points": [25, 50, 75],
                "timeout": 300,
            },
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
        resp = client.post(
            "/api/task",
            json={
                "magnet": "magnet:?xt=urn:btih:aabbccddee11223344556677889900aabbccddee",
                "sample_points": [0, 100],
            },
        )
        assert resp.status_code == 400

    def test_too_many_sample_points(self, client):
        resp = client.post(
            "/api/task",
            json={
                "magnet": "magnet:?xt=urn:btih:aabbccddee11223344556677889900aabbccddee",
                "sample_points": list(range(1, 25)),
            },
        )
        assert resp.status_code == 400

    def test_invalid_timeout(self, client):
        resp = client.post(
            "/api/task",
            json={
                "magnet": "magnet:?xt=urn:btih:aabbccddee11223344556677889900aabbccddee",
                "timeout": 10,
            },
        )
        assert resp.status_code == 400


class TestGetTask:
    def test_existing_task(self, client):
        resp = client.post(
            "/api/task",
            json={"magnet": "magnet:?xt=urn:btih:aabbccddee11223344556677889900aabbccddee"},
        )
        task_id = resp.get_json()["task_id"]

        resp = client.get(f"/api/task/{task_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["task_id"] == task_id
        assert data["status"] == "queued"
        assert "snapshots" in data

    def test_nonexistent_task(self, client):
        resp = client.get("/api/task/doesnotexist")
        assert resp.status_code == 404


class TestGetSnapshots:
    def test_empty_snapshots(self, client):
        resp = client.post(
            "/api/task",
            json={"magnet": "magnet:?xt=urn:btih:aabbccddee11223344556677889900aabbccddee"},
        )
        task_id = resp.get_json()["task_id"]

        resp = client.get(f"/api/task/{task_id}/snapshots")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["snapshots"] == []

    def test_nonexistent_task(self, client):
        resp = client.get("/api/task/nope/snapshots")
        assert resp.status_code == 404


class TestDeleteTask:
    def test_delete_existing(self, client):
        resp = client.post(
            "/api/task",
            json={"magnet": "magnet:?xt=urn:btih:aabbccddee11223344556677889900aabbccddee"},
        )
        task_id = resp.get_json()["task_id"]

        resp = client.delete(f"/api/task/{task_id}")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "cancelled"

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/task/nope")
        assert resp.status_code == 404


class TestTaskStore:
    def test_create_and_get(self, task_store):
        task_store.create_task("t1", "magnet:test", [25, 50, 75], 600)
        task = task_store.get_task("t1")
        assert task is not None
        assert task["task_id"] == "t1"
        assert task["status"] == "queued"
        assert len(task["snapshots"]) == 3

    def test_update_task(self, task_store):
        task_store.create_task("t2", "magnet:test", [50], 600)
        task_store.update_task("t2", status="downloading", video_file="test.mp4")
        task = task_store.get_task("t2")
        assert task["status"] == "downloading"
        assert task["video_file"] == "test.mp4"

    def test_update_snapshot(self, task_store):
        task_store.create_task("t3", "magnet:test", [50], 600)
        task_store.update_snapshot("t3", 50, "snap_50.jpg", "ready")
        task = task_store.get_task("t3")
        snap = task["snapshots"][0]
        assert snap["filename"] == "snap_50.jpg"
        assert snap["status"] == "ready"
        assert snap["url"] == "/snapshots/t3/snap_50.jpg"

    def test_get_nonexistent(self, task_store):
        assert task_store.get_task("nope") is None

    def test_delete_task(self, task_store):
        task_store.create_task("t4", "magnet:test", [50], 600)
        task_store.delete_task("t4")
        assert task_store.get_task("t4") is None
