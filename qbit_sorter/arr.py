"""Notify Sonarr / Radarr to import completed downloads.

After qBittorrent torrents are categorized, we ask each configured *arr
instance to process its download-client queue (default command
``RefreshMonitoredDownloads``), which makes it import anything that has
finished. Both Sonarr and Radarr expose this at ``/api/v3/command``.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from .client import QbitClient
from .config import ArrServiceConfig, Config

log = logging.getLogger(__name__)


def _post_command(svc: ArrServiceConfig, timeout: float = 20.0) -> dict[str, Any]:
    url = f"{svc.url}/api/v3/command"
    resp = requests.post(
        url,
        json={"name": svc.command},
        headers={"X-Api-Key": svc.api_key},
        timeout=timeout,
    )
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return {}


def trigger_service(svc: ArrServiceConfig, dry_run: bool) -> dict[str, Any]:
    """Send the configured command to one *arr service."""
    if dry_run:
        log.info("[dry-run] would POST %s -> %s (%s)", svc.command, svc.name, svc.url)
        return {"service": svc.name, "command": svc.command,
                "triggered": False, "dry_run": True, "note": "preview"}
    try:
        data = _post_command(svc)
        cmd_id = data.get("id")
        log.info("Triggered %s on %s%s", svc.command, svc.name,
                 f" (command id {cmd_id})" if cmd_id else "")
        return {"service": svc.name, "command": svc.command,
                "triggered": True, "id": cmd_id, "note": ""}
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to trigger %s on %s: %s", svc.command, svc.name, exc)
        return {"service": svc.name, "command": svc.command,
                "triggered": False, "note": str(exc)}


def trigger_for_completed(
    cfg: Config, client: QbitClient, only_categories: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Trigger each enabled *arr service that has completed torrents in its
    category. If `only_categories` is given, restrict to those categories
    (used by the webhook, which knows the single torrent's category)."""
    results: list[dict[str, Any]] = []
    enabled = [s for s in cfg.arr if s.enabled]
    if not enabled:
        return results

    for svc in enabled:
        if only_categories is not None and svc.category not in only_categories:
            continue
        try:
            completed = client.completed_in_category(svc.category)
        except Exception as exc:  # noqa: BLE001
            log.error("Could not list category '%s' for %s: %s",
                      svc.category, svc.name, exc)
            continue
        if not completed and only_categories is None:
            log.debug("No completed torrents in '%s' — not triggering %s.",
                      svc.category, svc.name)
            continue
        results.append(trigger_service(svc, cfg.dry_run))
    return results
