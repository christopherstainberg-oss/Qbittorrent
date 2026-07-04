"""Load and validate the YAML configuration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Valid qBittorrent torrent-list filters we accept in `states`.
VALID_STATES = {
    "all", "downloading", "seeding", "completed", "paused", "stopped",
    "active", "inactive", "resumed", "stalled", "stalled_uploading",
    "stalled_downloading", "errored",
}


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


@dataclass
class Rule:
    """One categorization rule. All specified conditions must match (AND)."""

    name: str
    category: str
    name_regex: re.Pattern | None = None
    name_contains: list[str] = field(default_factory=list)
    tracker_contains: list[str] = field(default_factory=list)
    save_path_contains: list[str] = field(default_factory=list)
    category_is: list[str] | None = None
    min_size_gb: float | None = None
    max_size_gb: float | None = None
    save_path: str | None = None  # used only when creating a missing category


@dataclass
class QbitConfig:
    host: str
    username: str
    password: str
    verify_cert: bool = True


@dataclass
class AudiobookConfig:
    """Post-processing for completed torrents in the audiobook category:
    normalize each into `<folder_template>` and (for single files) rename the
    file to `<file_template>`, using the qBittorrent name parsed as
    'Title <delimiter> Author'."""

    enabled: bool = False
    category: str = "Audiobooks"
    delimiter: str = " - "
    folder_template: str = "{author}/{title}"
    file_template: str = "{title}"
    sanitize: bool = True


@dataclass
class ArrServiceConfig:
    """A Sonarr/Radarr instance to notify after downloads are categorized."""

    name: str            # "sonarr" | "radarr" (label)
    enabled: bool
    url: str
    api_key: str
    category: str        # trigger when completed torrents have this category
    command: str = "RefreshMonitoredDownloads"


@dataclass
class PollConfig:
    enabled: bool = False
    interval_minutes: float = 2.0


@dataclass
class Config:
    qbittorrent: QbitConfig
    states: list[str]
    rules: list[Rule]
    dry_run: bool = True
    enable_autotmm: bool = True
    create_missing_categories: bool = False
    default_category: str | None = None
    audiobooks: AudiobookConfig = field(default_factory=AudiobookConfig)
    arr: list[ArrServiceConfig] = field(default_factory=list)
    poll: PollConfig = field(default_factory=PollConfig)


def _as_str_list(value: Any, where: str) -> list[str]:
    """Coerce a scalar or list into a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    raise ConfigError(f"{where} must be a string or a list of strings")


def _parse_rule(raw: dict, index: int) -> Rule:
    if not isinstance(raw, dict):
        raise ConfigError(f"rules[{index}] must be a mapping")

    category = raw.get("category")
    if not category:
        raise ConfigError(f"rules[{index}] is missing required 'category'")

    name_regex = None
    if raw.get("name_regex"):
        try:
            name_regex = re.compile(str(raw["name_regex"]), re.IGNORECASE)
        except re.error as exc:
            raise ConfigError(
                f"rules[{index}].name_regex is not a valid regex: {exc}"
            ) from exc

    category_is = None
    if "category_is" in raw and raw["category_is"] is not None:
        category_is = _as_str_list(raw["category_is"], f"rules[{index}].category_is")

    return Rule(
        name=str(raw.get("name", f"rule {index + 1}")),
        category=str(category),
        name_regex=name_regex,
        name_contains=_as_str_list(raw.get("name_contains"), f"rules[{index}].name_contains"),
        tracker_contains=_as_str_list(raw.get("tracker_contains"), f"rules[{index}].tracker_contains"),
        save_path_contains=_as_str_list(raw.get("save_path_contains"), f"rules[{index}].save_path_contains"),
        category_is=category_is,
        min_size_gb=raw.get("min_size_gb"),
        max_size_gb=raw.get("max_size_gb"),
        save_path=raw.get("save_path"),
    )


def rule_to_dict(rule: Rule) -> dict:
    """Serialize a Rule back to a plain dict (only fields that are set),
    suitable for the API and for writing to YAML."""
    d: dict[str, Any] = {"name": rule.name, "category": rule.category}
    if rule.name_regex is not None:
        d["name_regex"] = rule.name_regex.pattern
    if rule.name_contains:
        d["name_contains"] = list(rule.name_contains)
    if rule.tracker_contains:
        d["tracker_contains"] = list(rule.tracker_contains)
    if rule.save_path_contains:
        d["save_path_contains"] = list(rule.save_path_contains)
    if rule.category_is is not None:
        d["category_is"] = list(rule.category_is)
    if rule.min_size_gb is not None:
        d["min_size_gb"] = rule.min_size_gb
    if rule.max_size_gb is not None:
        d["max_size_gb"] = rule.max_size_gb
    if rule.save_path:
        d["save_path"] = rule.save_path
    return d


def validate_rules(raw_rules: list) -> list[dict]:
    """Validate a list of rule dicts (e.g. from the API), raising ConfigError
    on any problem. Returns cleaned dicts safe to persist."""
    if not isinstance(raw_rules, list):
        raise ConfigError("'rules' must be a list")
    cleaned: list[dict] = []
    for i, raw in enumerate(raw_rules):
        rule = _parse_rule(raw, i)          # reuses all the validation above
        cleaned.append(rule_to_dict(rule))
    return cleaned


def load_config(path: str | Path) -> Config:
    """Read, parse and validate the config file at `path`."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(
            f"Config file not found: {path}\n"
            "Copy config.example.yaml to config.yaml and edit it."
        )

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ConfigError("Top level of config must be a mapping")

    qb = raw.get("qbittorrent")
    if not isinstance(qb, dict):
        raise ConfigError("Missing required 'qbittorrent' section")
    for key in ("host", "username", "password"):
        if not qb.get(key):
            raise ConfigError(f"qbittorrent.{key} is required")

    qbit = QbitConfig(
        # Environment variables win over the file so secrets can be injected
        # by Docker/compose without being written into config.yaml.
        host=os.getenv("QBIT_HOST", str(qb["host"])),
        username=os.getenv("QBIT_USERNAME", str(qb["username"])),
        password=os.getenv("QBIT_PASSWORD", str(qb["password"])),
        verify_cert=_env_bool("QBIT_VERIFY_CERT", bool(qb.get("verify_cert", True))),
    )

    states = _as_str_list(raw.get("states"), "states")
    if not states:
        states = ["completed", "seeding"]
    for state in states:
        if state not in VALID_STATES:
            raise ConfigError(
                f"Unknown state '{state}'. Valid states: {', '.join(sorted(VALID_STATES))}"
            )

    raw_rules = raw.get("rules") or []
    if not isinstance(raw_rules, list):
        raise ConfigError("'rules' must be a list")
    rules = [_parse_rule(r, i) for i, r in enumerate(raw_rules)]

    default_category = raw.get("default_category")
    if default_category is not None:
        default_category = str(default_category)

    ab_raw = raw.get("audiobooks") or {}
    if not isinstance(ab_raw, dict):
        raise ConfigError("'audiobooks' must be a mapping")
    defaults = AudiobookConfig()
    audiobooks = AudiobookConfig(
        enabled=bool(ab_raw.get("enabled", defaults.enabled)),
        category=str(ab_raw.get("category", defaults.category)),
        delimiter=str(ab_raw.get("delimiter", defaults.delimiter)),
        folder_template=str(ab_raw.get("folder_template", defaults.folder_template)),
        file_template=str(ab_raw.get("file_template", defaults.file_template)),
        sanitize=bool(ab_raw.get("sanitize", defaults.sanitize)),
    )

    arr = _parse_arr(raw.get("arr") or {})

    poll_raw = raw.get("poll") or {}
    if not isinstance(poll_raw, dict):
        raise ConfigError("'poll' must be a mapping")
    poll = PollConfig(
        enabled=_env_bool("POLL_ENABLED", bool(poll_raw.get("enabled", False))),
        interval_minutes=float(os.getenv("POLL_INTERVAL_MINUTES",
                                         poll_raw.get("interval_minutes", 2.0))),
    )

    return Config(
        qbittorrent=qbit,
        states=states,
        rules=rules,
        dry_run=_env_bool("DRY_RUN", bool(raw.get("dry_run", True))),
        enable_autotmm=bool(raw.get("enable_autotmm", True)),
        create_missing_categories=bool(raw.get("create_missing_categories", False)),
        default_category=default_category,
        audiobooks=audiobooks,
        arr=arr,
        poll=poll,
    )


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _parse_arr(raw: dict) -> list[ArrServiceConfig]:
    """Parse the `arr` section. Supports keys 'sonarr' and 'radarr', each a
    mapping. API keys and URLs may be overridden via SONARR_/RADARR_ env vars."""
    if not isinstance(raw, dict):
        raise ConfigError("'arr' must be a mapping")
    services: list[ArrServiceConfig] = []
    for name in ("sonarr", "radarr"):
        svc = raw.get(name)
        if not isinstance(svc, dict):
            continue
        prefix = name.upper()  # SONARR / RADARR
        url = os.getenv(f"{prefix}_URL", str(svc.get("url", ""))).rstrip("/")
        api_key = os.getenv(f"{prefix}_API_KEY", str(svc.get("api_key", "")))
        enabled = _env_bool(f"{prefix}_ENABLED", bool(svc.get("enabled", False)))
        default_cat = "TV-Sonarr" if name == "sonarr" else "Movies - Radarr"
        services.append(ArrServiceConfig(
            name=name,
            enabled=enabled,
            url=url,
            api_key=api_key,
            category=str(svc.get("category", default_cat)),
            command=str(svc.get("command", "RefreshMonitoredDownloads")),
        ))
        if enabled and (not url or not api_key):
            raise ConfigError(
                f"arr.{name} is enabled but url/api_key is missing "
                f"(set them in config.yaml or {prefix}_URL / {prefix}_API_KEY)."
            )
    return services
