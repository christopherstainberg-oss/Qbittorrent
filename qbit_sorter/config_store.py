"""Persist edits back to config.yaml while preserving comments/formatting.

Uses ruamel.yaml round-trip loading so hand-written comments in the config
survive edits made through the web UI. Only the keys we touch are changed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .config import ConfigError

log = logging.getLogger(__name__)

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)
# Never line-wrap: long values like regexes must stay on one line so they
# remain readable and can't be mangled by fragile backslash continuations.
_yaml.width = 4096


def _load(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = _yaml.load(fh)
    return data if data is not None else {}


def _dump(path: Path, data: Any) -> None:
    try:
        with path.open("w", encoding="utf-8") as fh:
            _yaml.dump(data, fh)
    except OSError as exc:
        raise ConfigError(
            f"Could not write {path}: {exc}. If running in Docker, mount "
            "config.yaml read-write (not ':ro')."
        ) from exc
    log.info("Saved config changes to %s", path)


def save_rules(path: str | Path, rules: list[dict]) -> None:
    path = Path(path)
    data = _load(path)
    data["rules"] = rules
    _dump(path, data)


def save_automations(path: str | Path, automations: list[dict]) -> None:
    """Persist trigger->action automations (already validated by the caller)."""
    path = Path(path)
    data = _load(path)
    data["automations"] = automations
    _dump(path, data)


def save_settings(path: str | Path, *, dry_run: bool | None = None,
                  poll: dict | None = None) -> None:
    path = Path(path)
    data = _load(path)
    if dry_run is not None:
        data["dry_run"] = bool(dry_run)
    if poll:
        node = data.get("poll")
        if not isinstance(node, dict):
            node = {}
            data["poll"] = node
        if "enabled" in poll:
            node["enabled"] = bool(poll["enabled"])
        if "interval_minutes" in poll:
            node["interval_minutes"] = float(poll["interval_minutes"])
    _dump(path, data)


def save_arr(path: str | Path, services: dict[str, dict]) -> None:
    """services: {'sonarr': {...fields...}, 'radarr': {...}}"""
    path = Path(path)
    data = _load(path)
    arr = data.get("arr")
    if not isinstance(arr, dict):
        arr = {}
        data["arr"] = arr
    allowed = {"enabled", "url", "api_key", "category", "command"}
    for name, fields in services.items():
        if name not in ("sonarr", "radarr"):
            continue
        node = arr.get(name)
        if not isinstance(node, dict):
            node = {}
            arr[name] = node
        for key, val in fields.items():
            if key in allowed:
                node[key] = val
    _dump(path, data)


def save_relocation(path: str | Path, cfg: dict) -> None:
    """Persist the library-relocation config (Sonarr/Radarr-style destinations).

    Validates *before* writing so a bad request can never leave config.yaml in
    an unloadable state (blank rows are dropped; bad modes raise)."""
    cleaned_dests = None
    if "destinations" in cfg:
        cleaned_dests = []
        for d in cfg["destinations"]:
            category, dpath = d.get("category"), d.get("path")
            if not category or not dpath:
                continue  # drop incomplete rows the UI may send
            mode = str(d.get("mode", "hardlink")).lower()
            if mode not in ("hardlink", "copy", "move"):
                raise ConfigError(
                    f"destination '{category}' has invalid mode '{mode}' "
                    "(use hardlink, copy or move)")
            cleaned_dests.append({"category": str(category), "path": str(dpath),
                                  "mode": mode})

    path = Path(path)
    data = _load(path)
    node = data.get("relocation")
    if not isinstance(node, dict):
        node = {}
        data["relocation"] = node
    if "enabled" in cfg:
        node["enabled"] = bool(cfg["enabled"])
    if "qbit_download_root" in cfg:
        node["qbit_download_root"] = str(cfg["qbit_download_root"])
    if "local_download_root" in cfg:
        node["local_download_root"] = str(cfg["local_download_root"])
    if cleaned_dests is not None:
        node["destinations"] = cleaned_dests
    _dump(path, data)
