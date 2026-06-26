import threading
import libtorrent as lt
from config import LIBTORRENT_SETTINGS, DOWNLOAD_DIR


class SessionManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        settings = {
            "listen_interfaces": "0.0.0.0:{}-{}".format(
                LIBTORRENT_SETTINGS["listen_port_range"][0],
                LIBTORRENT_SETTINGS["listen_port_range"][1],
            ),
            "download_rate_limit": LIBTORRENT_SETTINGS["download_rate_limit"],
            "connections_limit": LIBTORRENT_SETTINGS["connections_limit"],
            "enable_dht": True,
            "enable_lsd": True,
            "enable_natpmp": True,
            "enable_upnp": True,
            "alert_mask": (
                lt.alert.category_t.status_notification
                | lt.alert.category_t.error_notification
                | lt.alert.category_t.piece_progress_notification
            ),
        }
        self._session = lt.session(settings)
        self._handles = {}
        self._handles_lock = threading.Lock()

    @property
    def session(self) -> lt.session:
        return self._session

    def add_torrent(self, params: lt.add_torrent_params) -> lt.torrent_handle:
        params.save_path = DOWNLOAD_DIR
        handle = self._session.add_torrent(params)
        info_hash = str(handle.info_hash())
        with self._handles_lock:
            self._handles[info_hash] = handle
        return handle

    def remove_torrent(self, handle: lt.torrent_handle, delete_files: bool = True):
        info_hash = str(handle.info_hash())
        with self._handles_lock:
            self._handles.pop(info_hash, None)
        flags = lt.session.delete_files if delete_files else 0
        self._session.remove_torrent(handle, flags)

    def get_handle(self, info_hash: str):
        with self._handles_lock:
            return self._handles.get(info_hash)

    def pop_alerts(self):
        return self._session.pop_alerts()

    def shutdown(self):
        with self._handles_lock:
            for handle in list(self._handles.values()):
                try:
                    self._session.remove_torrent(handle, lt.session.delete_files)
                except Exception:
                    pass
            self._handles.clear()
