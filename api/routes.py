import os
import uuid
from flask import Blueprint, request, jsonify, send_from_directory, abort
from api.schemas import validate_task_request
from core.torrent_parser import extract_info_hash
from storage.task_store import TaskStore
import config

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

    info_hash = extract_info_hash(validated["magnet"])
    task_id = uuid.uuid4().hex[:8]
    sample_points = validated["sample_points"]

    store.create_task(
        task_id=task_id,
        info_hash=info_hash,
        magnet=validated["magnet"],
        sample_points=sample_points,
        timeout=validated["timeout"],
    )

    if store.has_cached_snapshots(info_hash, sample_points):
        store.fill_from_cache(task_id, info_hash, sample_points)
        return jsonify({
            "task_id": task_id,
            "status": "completed",
            "message": "Cache hit",
        }), 200

    queue_len = len(queue)
    if queue_len >= config.TASK_SETTINGS["max_queue_size"]:
        abort(503, description="Task queue is full, try again later")

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

    info_hash = task.get("info_hash", task_id)
    snapshots = []
    snap_dir = os.path.join(config.SNAPSHOT_DIR, info_hash)
    if os.path.isdir(snap_dir):
        for fname in sorted(os.listdir(snap_dir)):
            if fname.endswith(".jpg"):
                snapshots.append({
                    "filename": fname,
                    "url": f"/snapshots/{info_hash}/{fname}",
                })

    return jsonify({"task_id": task_id, "snapshots": snapshots})


@api_bp.route("/snapshots/<info_hash>/<filename>", methods=["GET"])
def serve_snapshot(info_hash, filename):
    snap_dir = os.path.join(config.SNAPSHOT_DIR, info_hash)
    if not os.path.isdir(snap_dir):
        abort(404)

    safe_filename = os.path.basename(filename)
    if not safe_filename.endswith(".jpg"):
        abort(400, description="Invalid filename")

    return send_from_directory(snap_dir, safe_filename)


@api_bp.route("/api/task/<task_id>", methods=["DELETE"])
def cancel_task(task_id):
    store = get_task_store()
    task = store.get_task(task_id)
    if not task:
        abort(404)

    store.update_task(task_id, status="cancelled")
    return jsonify({"task_id": task_id, "status": "cancelled"})
