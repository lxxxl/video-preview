import os
import sys
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock libtorrent before any core module imports it
mock_lt = types.ModuleType("libtorrent")
mock_lt.session = MagicMock
mock_lt.add_torrent_params = MagicMock
mock_lt.torrent_handle = MagicMock
mock_lt.torrent_flags = MagicMock()
mock_lt.torrent_flags.upload_mode = 0
mock_lt.parse_magnet_uri = MagicMock(return_value=MagicMock())
mock_lt.read_piece_alert = type("read_piece_alert", (), {})
mock_lt.alert = MagicMock()
mock_lt.alert.category_t = MagicMock()
mock_lt.alert.category_t.status_notification = 1
mock_lt.alert.category_t.error_notification = 2
mock_lt.alert.category_t.piece_progress_notification = 4
sys.modules["libtorrent"] = mock_lt

import pytest
import config


@pytest.fixture
def tmp_dirs(tmp_path):
    config.SNAPSHOT_DIR = str(tmp_path / "snapshots")
    config.TEMP_DIR = str(tmp_path / "tmp")
    config.DOWNLOAD_DIR = str(tmp_path / "downloads")
    config.DATABASE_PATH = str(tmp_path / "test.db")
    os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)
    return tmp_path


@pytest.fixture
def task_store(tmp_dirs):
    from storage.task_store import TaskStore
    store = TaskStore(db_path=str(tmp_dirs / "test.db"))
    yield store
    store.close()


@pytest.fixture
def app(tmp_dirs):
    from flask import Flask
    from api.routes import api_bp
    from api.errors import register_error_handlers
    from storage.task_store import TaskStore

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.config["TASK_STORE"] = TaskStore(db_path=str(tmp_dirs / "test.db"))

    mock_queue = MagicMock()
    mock_queue.__len__ = MagicMock(return_value=0)
    flask_app.config["TASK_QUEUE"] = mock_queue

    flask_app.register_blueprint(api_bp)
    register_error_handlers(flask_app)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()
