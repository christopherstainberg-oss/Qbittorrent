"""Thin wrapper around qbittorrent-api with the operations we need."""

from __future__ import annotations

import logging
from typing import Any

import qbittorrentapi

from .config import QbitConfig

log = logging.getLogger(__name__)


class QbitClient:
    def __init__(self, cfg: QbitConfig):
        self._client = qbittorrentapi.Client(
            host=cfg.host,
            username=cfg.username,
            password=cfg.password,
            VERIFY_WEBUI_CERTIFICATE=cfg.verify_cert,
            REQUESTS_ARGS={"timeout": 30},
        )

    def connect(self) -> None:
        """Authenticate; raises on bad host/credentials."""
        try:
            self._client.auth_log_in()
        except qbittorrentapi.LoginFailed as exc:
            raise RuntimeError(f"qBittorrent login failed: {exc}") from exc
        except qbittorrentapi.APIConnectionError as exc:
            raise RuntimeError(
                f"Could not reach qBittorrent WebUI: {exc}\n"
                "Check the host/port and that the WebUI is enabled."
            ) from exc
        ver = self._client.app.version
        api_ver = self._client.app.web_api_version
        log.info("Connected to qBittorrent %s (WebAPI %s)", ver, api_ver)

    def torrents(self, states: list[str]) -> list[Any]:
        """Return torrents matching any of the given state filters (deduped)."""
        by_hash: dict[str, Any] = {}
        for state in states:
            for t in self._client.torrents_info(status_filter=state):
                by_hash[t.get("hash", "")] = t
        return list(by_hash.values())

    def categories(self) -> dict[str, Any]:
        """Return the categories configured in qBittorrent (name -> info)."""
        return dict(self._client.torrents_categories())

    def create_category(self, name: str, save_path: str | None = None) -> None:
        self._client.torrents_create_category(name=name, save_path=save_path or "")

    def edit_category(self, name: str, save_path: str | None = None) -> None:
        self._client.torrents_edit_category(name=name, save_path=save_path or "")

    def default_save_path(self) -> str:
        """qBittorrent's global default save path (where torrents with no
        category-specific path are stored)."""
        return self._client.app.preferences.get("save_path", "") or ""

    def set_default_save_path(self, path: str) -> None:
        self._client.app_set_preferences(prefs={"save_path": path})

    def set_location(self, location: str, hashes: list[str]) -> None:
        """Relocate torrents' data to an explicit folder (qBittorrent
        'Set Location'). This physically moves the files and switches the
        torrent(s) to manual mode (Automatic Torrent Management off)."""
        self._client.torrents_set_location(location=location, torrent_hashes=hashes)

    def set_category(self, category: str, hashes: list[str]) -> None:
        self._client.torrents_set_category(category=category, torrent_hashes=hashes)

    def relocate(self, save_path: str, hashes: list[str]) -> None:
        """Physically move torrents into `save_path`, then keep them under
        Automatic Torrent Management.

        Unlike enabling AutoTMM on its own — which qBittorrent accepts even when
        the destination is unwritable and then fails the move *silently* — this
        issues an explicit Set Location, so qBittorrent raises HTTP 409
        ("Cannot make save path") when it can't write there. That lets callers
        report a failed relocation instead of falsely claiming success. Once the
        data is in place we re-enable AutoTMM so the torrent stays managed."""
        self._client.torrents_set_location(location=save_path, torrent_hashes=hashes)
        self._client.torrents_set_auto_management(enable=True, torrent_hashes=hashes)

    # ---- Download-queue priority -------------------------------------------
    # These reorder torrents in qBittorrent's download/seed queue. They only
    # have an effect when Torrent Queueing is enabled; with it off qBittorrent
    # returns HTTP 409, which callers surface as a helpful message.

    def queueing_enabled(self) -> bool:
        """Whether qBittorrent's Torrent Queueing is turned on (required for
        queue-priority changes to take effect)."""
        return bool(self._client.app.preferences.get("queueing_enabled", False))

    def set_queueing_enabled(self, enabled: bool) -> None:
        self._client.app_set_preferences(prefs={"queueing_enabled": bool(enabled)})

    def top_priority(self, hashes: list[str]) -> None:
        """Move torrents to the top of the queue (download/seed first)."""
        self._client.torrents_top_priority(torrent_hashes=hashes)

    def bottom_priority(self, hashes: list[str]) -> None:
        """Move torrents to the bottom of the queue (download/seed last)."""
        self._client.torrents_bottom_priority(torrent_hashes=hashes)

    def increase_priority(self, hashes: list[str]) -> None:
        """Move torrents one step up the queue (higher priority / sooner)."""
        self._client.torrents_increase_priority(torrent_hashes=hashes)

    def decrease_priority(self, hashes: list[str]) -> None:
        """Move torrents one step down the queue (lower priority / later)."""
        self._client.torrents_decrease_priority(torrent_hashes=hashes)

    # ---- Per-file download priority ----------------------------------------
    # qBittorrent priority codes: 0 = do not download, 1 = normal, 6 = high,
    # 7 = maximal. A torrent shows "Mixed" in its UI when its files don't all
    # share the same priority; setting one level makes them uniform again.

    def set_all_files_priority(self, torrent_hash: str, priority: int) -> int:
        """Set every file in a torrent to `priority`. Returns the number of
        files changed (0 if the torrent has no files yet, e.g. still fetching
        metadata)."""
        files = self._client.torrents_files(torrent_hash=torrent_hash)
        ids = [f.get("index", i) for i, f in enumerate(files)]
        if not ids:
            return 0
        self._client.torrents_file_priority(
            torrent_hash=torrent_hash, file_ids=ids, priority=priority)
        return len(ids)

    def enable_autotmm(self, hashes: list[str]) -> None:
        self._client.torrents_set_auto_management(enable=True, torrent_hashes=hashes)

    def completed_in_category(self, category: str) -> list[Any]:
        """Completed torrents belonging to a given category."""
        return list(self._client.torrents_info(
            status_filter="completed", category=category))

    def files(self, torrent_hash: str) -> list[Any]:
        """File list for a torrent; each item's `.name` is the path relative
        to the torrent's save root."""
        return list(self._client.torrents_files(torrent_hash=torrent_hash))

    def rename_file(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        self._client.torrents_rename_file(
            torrent_hash=torrent_hash, old_path=old_path, new_path=new_path)

    def rename_folder(self, torrent_hash: str, old_path: str, new_path: str) -> None:
        self._client.torrents_rename_folder(
            torrent_hash=torrent_hash, old_path=old_path, new_path=new_path)
