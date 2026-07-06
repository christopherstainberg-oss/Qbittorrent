"""Match torrents against categorization rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Rule

_BYTES_PER_GB = 1024 ** 3


@dataclass
class TorrentView:
    """The subset of a torrent's fields our rules care about."""

    hash: str
    name: str
    category: str
    save_path: str
    tracker: str
    size: int  # bytes
    state: str
    priority: int  # queue position; 0 (or negative) means not queued

    @classmethod
    def from_api(cls, t: Any) -> "TorrentView":
        return cls(
            hash=t.get("hash", ""),
            name=t.get("name", ""),
            category=t.get("category", "") or "",
            save_path=t.get("save_path", "") or t.get("content_path", "") or "",
            tracker=t.get("tracker", "") or "",
            size=int(t.get("size", 0) or 0),
            state=t.get("state", "") or "",
            priority=int(t.get("priority", 0) or 0),
        )


def _contains_any(haystack: str, needles: list[str]) -> bool:
    lowered = haystack.lower()
    return any(n.lower() in lowered for n in needles)


def rule_matches(rule: Rule, t: TorrentView) -> bool:
    """Return True if the torrent satisfies every condition set on the rule."""
    if rule.name_regex is not None and not rule.name_regex.search(t.name):
        return False
    if rule.name_contains and not _contains_any(t.name, rule.name_contains):
        return False
    if rule.tracker_contains and not _contains_any(t.tracker, rule.tracker_contains):
        return False
    if rule.save_path_contains and not _contains_any(t.save_path, rule.save_path_contains):
        return False
    if rule.category_is is not None and t.category not in rule.category_is:
        return False
    if rule.min_size_gb is not None and t.size < rule.min_size_gb * _BYTES_PER_GB:
        return False
    if rule.max_size_gb is not None and t.size > rule.max_size_gb * _BYTES_PER_GB:
        return False
    return True


def match_torrent(rules: list[Rule], t: TorrentView) -> Rule | None:
    """Return the first matching rule, or None."""
    for rule in rules:
        if rule_matches(rule, t):
            return rule
    return None
