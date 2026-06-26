import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask
import structlog

import config
from api.routes import api_bp
from api.errors import register_error_handlers
from storage.task_store import TaskStore

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)

logger = structlog.get_logger(__name__)


def create_app() -> Flask:
    app = Flask(__name__)

    for d in [config.SNAPSHOT_DIR, config.TEMP_DIR, config.DOWNLOAD_DIR]:
        os.makedirs(d, exist_ok=True)

    app.config["TASK_STORE"] = TaskStore()

    try:
        from redis import Redis
        from rq import Queue
        redis_conn = Redis.from_url(config.REDIS_URL)
        redis_conn.ping()
        app.config["TASK_QUEUE"] = Queue(connection=redis_conn)
        logger.info("redis_connected", url=config.REDIS_URL)
    except Exception as e:
        app.config["TASK_QUEUE"] = None
        logger.warning("redis_unavailable", error=str(e), url=config.REDIS_URL)

    try:
        from flask_limiter import Limiter
        from flask_limiter.util import get_remote_address
        Limiter(
            app=app,
            key_func=get_remote_address,
            default_limits=[f"{config.RATE_LIMIT['global_per_minute']} per minute"],
            storage_uri=config.REDIS_URL,
            swallow_errors=True,
        )
    except Exception:
        pass

    app.register_blueprint(api_bp)
    register_error_handlers(app)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
