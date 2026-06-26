import os
import uuid
from flask import Blueprint, request, jsonify, send_from_directory, abort
from api.schemas import validate_task_request
from storage.task_store import TaskStore
from config import SNAPSHOT_DIR, TASK_SETTINGS

api_bp = Blueprint("api", __name__)


def get_task_store() -> TaskStore:
    from flask import current_app
    return current_app.config["TASK_STORE"]


def get_task_queue():
    from flask import current_app
    return current_app.config["TASK_QUEUE"]


@api_bp.route("/api/task", methods=["POST"])
def create_task():
    data = request.get_json(silent=True)
    validated, error = validate_task_request(data)
    if error:
        abort(400, description=error)

    store = get_task_store()
    queue = get_task_queue()

    queue_len = len(queue)
    if queue_len >= TASK_SETTINGS["max_queue_size"]:
        abort(503, description="Task queue is full, try again later")

    task_id = uuid.uuid4().hex[:8]
    store.create_task(
        task_id=task_id,
        magnet=validated["magnet"],
        sample_points=validated["sample_points"],
        timeout=validated["timeout"],
    )

    from worker import process_task
    queue.enqueue(
        process_task,
        task_id,
        validated["magnet"],
        validated["sample_points"],
        validated["timeout"],
        job_id=task_id,
        job_timeout=validated["timeout"] + 120,
    )

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "message": "Task created",
    }), 201


@api_bp.route("/api/task/<task_id>", methods=["GET"])
def get_task(task_id):
    store = get_task_store()
    task = store.get_task(task_id)
    if not task:
        abort(404)
    return jsonify(task)


@api_bp.route("/api/task/<task_id>/snapshots", methods=["GET"])
def get_snapshots(task_id):
    store = get_task_store()
    task = store.get_task(task_id)
    if not task:
        abort(404)

    snapshots = []
    task_dir = os.path.join(SNAPSHOT_DIR, task_id)
    if os.path.isdir(task_dir):
        for fname in sorted(os.listdir(task_dir)):
            if fname.endswith(".jpg"):
                snapshots.append({
                    "filename": fname,
                    "url": f"/snapshots/{task_id}/{fname}",
                })

    return jsonify({"task_id": task_id, "snapshots": snapshots})


@api_bp.route("/snapshots/<task_id>/<filename>", methods=["GET"])
def serve_snapshot(task_id, filename):
    task_dir = os.path.join(SNAPSHOT_DIR, task_id)
    if not os.path.isdir(task_dir):
        abort(404)

    safe_filename = os.path.basename(filename)
    if not safe_filename.endswith(".jpg"):
        abort(400, description="Invalid filename")

    return send_from_directory(task_dir, safe_filename)


@api_bp.route("/api/task/<task_id>", methods=["DELETE"])
def cancel_task(task_id):
    store = get_task_store()
    task = store.get_task(task_id)
    if not task:
        abort(404)

    store.update_task(task_id, status="cancelled")

    task_dir = os.path.join(SNAPSHOT_DIR, task_id)
    if os.path.isdir(task_dir):
        import shutil
        shutil.rmtree(task_dir, ignore_errors=True)

    return jsonify({"task_id": task_id, "status": "cancelled"})
