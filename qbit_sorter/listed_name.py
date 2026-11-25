"""Rename a torrent's content folder to the name listed in qBittorrent.

The list name (`torrents/info` `name`) and the folder under the save path
are different fields. This plans a `renameFolder` so the folder follows the
list. It does not parse Title/Author and it does not touch other categories.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Illegal on the Windows share that mounts this library (Z:\Audiobooks).
_UNSAFE = set('/\\:*?"<>|')


@dataclass
class ListedNamePlan:
    skipped: bool
    old: str = ""
    new: str = ""
    note: str = ""


def _relative(content_path: str, save_path: str) -> str:
    content = content_path.rstrip("/")
    save = (save_path or "").rstrip("/")
    if save and (content == save or content.startswith(save + "/")):
        return content[len(save):].lstrip("/")
    return content.rsplit("/", 1)[-1]


def _unsafe(name: str) -> bool:
    if not name or name in (".", ".."):
        return True
    if any(c in name for c in _UNSAFE):
        return True
    if name.endswith(" ") or name.endswith("."):
        return True
    return False


def plan_listed_rename(listed: str, content_path: str, save_path: str,
                       taken_names: set[str]) -> ListedNamePlan:
    """Decide one rename. `taken_names` are folder names already at save_path."""
    if not (content_path or "").strip():
        return ListedNamePlan(skipped=True, note="no content path")
    old = _relative(content_path, save_path)
    if not old:
        return ListedNamePlan(skipped=True, note="no content path")
    if old == listed:
        return ListedNamePlan(skipped=True, old=old, new=listed, note="already matches")
    if _unsafe(listed):
        return ListedNamePlan(skipped=True, old=old, note="listed name is not a folder name")
    if listed in taken_names and listed != old:
        return ListedNamePlan(skipped=True, old=old, new=listed,
                               note="target folder already exists")
    return ListedNamePlan(skipped=False, old=old, new=listed)


@dataclass
class _TorrentPlan:
    hash: str
    name: str
    plan: ListedNamePlan = field(default_factory=lambda: ListedNamePlan(skipped=True))


def build_plans(cfg, client) -> list[_TorrentPlan]:
    """One plan per torrent in the configured categories, any state."""
    section = cfg.listed_name
    plans: list[_TorrentPlan] = []
    claimed: set[str] = set()
    for category in section.categories:
        torrents = client.in_category(category)
        log.info("Listed-name: %d torrent(s) in '%s'.", len(torrents), category)
        taken = set()
        rows = []
        for t in torrents:
            content = (t.get("content_path") or "").rstrip("/")
            save = (t.get("save_path") or "").rstrip("/")
            rel = _relative(content, save) if content else ""
            top = rel.split("/", 1)[0] if rel else ""
            if top:
                taken.add(top)
            rows.append(t)
        for t in rows:
            listed = t.get("name") or ""
            plan = plan_listed_rename(
                listed=listed,
                content_path=t.get("content_path") or "",
                save_path=t.get("save_path") or "",
                taken_names=taken | claimed,
            )
            if not plan.skipped:
                claimed.add(plan.new)
            plans.append(_TorrentPlan(hash=t.get("hash") or "", name=listed, plan=plan))
    return plans


def _preview(cfg) -> bool:
    """Global dry_run previews this step unless listed_name.apply is set."""
    if getattr(getattr(cfg, "listed_name", None), "apply", False):
        return False
    return bool(getattr(cfg, "dry_run", True))


def apply_plans(cfg, client, plans: list[_TorrentPlan]) -> list[dict]:
    results: list[dict] = []
    preview = _preview(cfg)
    for item in plans:
        plan = item.plan
        if plan.skipped:
            log.info("skip: %s (%s)", item.name, plan.note)
            results.append({
                "name": item.name, "hash": item.hash, "skipped": True,
                "applied": False, "note": plan.note, "old": plan.old, "new": plan.new,
            })
            continue
        log.info("%srenameFolder: '%s' -> '%s'",
                 "[dry-run] " if preview else "", plan.old, plan.new)
        err = ""
        if not preview:
            try:
                client.rename_folder(item.hash, plan.old, plan.new)
            except Exception as exc:  # noqa: BLE001 — one bad torrent must not stop the poll
                err = str(exc)
                log.error("  failed: %s", exc)
        results.append({
            "name": item.name, "hash": item.hash, "skipped": False,
            "applied": (not preview) and not err,
            "note": err or ("preview" if preview else ""),
            "old": plan.old, "new": plan.new,
        })
    changed = sum(1 for r in results if not r["skipped"])
    log.info("%s %d listed-name rename(s).",
             "Would apply" if preview else "Applied", changed)
    return results


def sync(cfg, client) -> list[dict]:
    """Plan and apply listed-name renames. No-op when the section is disabled."""
    if not cfg.listed_name.enabled:
        return []
    return apply_plans(cfg, client, build_plans(cfg, client))
