"""User-defined trigger -> action automations.

Each automation reuses the same match conditions as categorization rules
(name/tracker/save-path/category/size). When a torrent matches, one action runs:
set a category, set file priority, nudge queue priority, add a tag, or relocate
to an explicit folder. Idempotent where it's cheap to check (category, tag).
"""

from __future__ import annotations

import logging
from typing import Any

from .client import QbitClient
from .config import Automation, AutomationAction, Config, Rule
from .rules import TorrentView, rule_matches

log = logging.getLogger(__name__)


def _as_rule(a: Automation) -> Rule:
    """Reuse rule matching for an automation's conditions."""
    return Rule(
        name=a.name, category="",
        name_regex=a.name_regex, name_contains=a.name_contains,
        tracker_contains=a.tracker_contains, save_path_contains=a.save_path_contains,
        category_is=a.category_is, min_size_gb=a.min_size_gb, max_size_gb=a.max_size_gb,
    )


def _apply(client: QbitClient, act: AutomationAction,
           matched: list[tuple[TorrentView, Any]], dry_run: bool) -> tuple[int, int, str]:
    """Run one action on the matched torrents. Returns (applied, skipped, note)."""
    p = act.params
    verb = "would" if dry_run else "did"
    hashes = [tv.hash for tv, _ in matched]

    if act.action == "set_category":
        target = p["category"]
        todo = [tv.hash for tv, _ in matched if tv.category != target]
        if todo and not dry_run:
            client.set_category(target, todo)
        return len(todo), len(matched) - len(todo), f"{verb} set category → '{target}'"

    if act.action == "add_tag":
        tag = p["tag"]
        todo = [tv.hash for tv, raw in matched
                if tag not in [x.strip() for x in (raw.get("tags", "") or "").split(",")]]
        if todo and not dry_run:
            client.add_tags(tag, todo)
        return len(todo), len(matched) - len(todo), f"{verb} add tag '{tag}'"

    if act.action == "file_priority":
        pri = int(p["priority"])
        if not dry_run:
            for h in hashes:
                client.set_all_files_priority(h, pri)
        return len(hashes), 0, f"{verb} set file priority {pri}"

    if act.action == "queue_priority":
        fn = {"top": client.top_priority, "up": client.increase_priority,
              "down": client.decrease_priority, "bottom": client.bottom_priority}[p["direction"]]
        if not dry_run:
            fn(hashes)
        return len(hashes), 0, f"{verb} move queue {p['direction']}"

    if act.action == "set_location":
        target = p["path"]
        if not dry_run:
            client.set_location(target, hashes)
        return len(hashes), 0, f"{verb} set location → '{target}'"

    return 0, len(matched), f"unknown action '{act.action}'"


def run(cfg: Config, client: QbitClient, dry_run: bool = True) -> list[dict]:
    """Evaluate every enabled automation and apply its action. Returns one
    result dict per automation."""
    enabled = [a for a in cfg.automations if a.enabled]
    if not enabled:
        return []

    # Fetch all torrents once and reuse across automations.
    views = [(TorrentView.from_api(t), t) for t in client.torrents(["all"])]
    results: list[dict] = []
    for a in enabled:
        rule = _as_rule(a)
        matched = []
        for tv, raw in views:
            if not rule_matches(rule, tv):
                continue
            if a.complete_only and float(raw.get("progress") or 0) < 1.0:
                continue
            matched.append((tv, raw))

        res: dict[str, Any] = {"name": a.name, "matched": len(matched),
                               "applied": 0, "actions": []}
        if not matched:
            res["note"] = "no matches"
            results.append(res)
            continue
        notes = []
        for act in a.actions:
            entry: dict[str, Any] = {"action": act.action, "applied": 0, "skipped": 0}
            try:
                applied, skipped, note = _apply(client, act, matched, dry_run)
                entry.update(applied=applied, skipped=skipped, note=note)
                res["applied"] += applied
            except Exception as exc:  # noqa: BLE001 — report, keep going
                entry["note"] = f"error — {exc}"
                log.warning("Automation '%s' action '%s' failed: %s", a.name, act.action, exc)
            res["actions"].append(entry)
            notes.append(entry["note"])
        res["note"] = "; ".join(notes)
        log.info("Automation '%s': matched %d — %s%s", a.name, len(matched),
                 res["note"], " [dry-run]" if dry_run else "")
        results.append(res)
    return results
