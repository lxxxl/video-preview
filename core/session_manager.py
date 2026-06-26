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
            "listen_interfaces": "0.0.0.0:{}".format(
                LIBTORRENT_SETTINGS["listen_port_range"][0],
            ),
            "download_rate_limit": LIBTORRENT_SETTINGS["download_rate_limit"],
            "upload_rate_limit": LIBTORRENT_SETTINGS.get("upload_rate_limit", 0),
            "connections_limit": LIBTORRENT_SETTINGS["connections_limit"],
            "enable_dht": True,
            "enable_lsd": True,
            "enable_natpmp": True,
            "enable_upnp": True,
            "dht_announce_interval": 60,
            "dht_search_branching": 10,
            "dht_max_peers_reply": 100,
            "min_announce_interval": 30,
            "min_reconnect_time": 1,
            "peer_connect_timeout": 10,
            "torrent_connect_boost": 30,
            # ── 方案 A: 加密 + 提升 peer 交换 ──
            "allow_multiple_connections_per_ip": True,
            "ignore_limits_on_local_network": False,
            "active_seeds": 0,
            "active_downloads": 3,
            "active_limit": 10,
            "active_tracker_limit": 5,
            "active_dht_limit": 10,
            "active_lsd_limit": 10,
            "out_enc_policy": lt.encryption_policy_t.enabled,
            "in_enc_policy": lt.encryption_policy_t.enabled,
            "allowed_enc_level": lt.encryption_level_t.both,
            "prefer_rc4": False,
            "alert_mask": (
                lt.alert.category_t.status_notification
                | lt.alert.category_t.error_notification
                | lt.alert.category_t.piece_progress_notification
                | lt.alert.category_t.peer_notification
            ),
        }
        self._session = lt.session(settings)

        dht_nodes = [
            ("router.bittorrent.com", 6881),
            ("router.utorrent.com", 6881),
            ("dht.transmissionbt.com", 6881),
            ("dht.aelitis.com", 6881),
            ("router.nuh.dev", 6881),
            ("dht.libtorrent.org", 25401),
        ]
        for host, port in dht_nodes:
            self._session.add_dht_router(host, port)

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
