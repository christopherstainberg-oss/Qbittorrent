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

    def set_category(self, category: str, hashes: list[str]) -> None:
        self._client.torrents_set_category(category=category, torrent_hashes=hashes)

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
