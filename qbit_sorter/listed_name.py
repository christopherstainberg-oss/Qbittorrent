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
# A path ending in one of these is a file, not a folder to rename.
_FILE_EXT = {
    ".m4b", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".wav", ".mp4", ".mkv",
    ".mobi", ".epub", ".pdf", ".zip", ".torrent", ".cue", ".nfo", ".jpg", ".png",
}
# These get their basename set to the qBittorrent list name. One file per
# extension only — a multi-track set is left as-is.
_RENAME_EXT = {".mp3", ".m4b", ".mobi", ".epub", ".pdf"}


@dataclass
class FileRename:
    old: str
    new: str


@dataclass
class ListedNamePlan:
    skipped: bool
    old: str = ""
    new: str = ""
    note: str = ""
    files: list[FileRename] = field(default_factory=list)


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


def _folder_to_rename(relative: str) -> str | None:
    """Content path of a single file points at the file. Rename its folder."""
    leaf = relative.rsplit("/", 1)[-1]
    ext = leaf[leaf.rfind("."):].lower() if "." in leaf else ""
    if ext not in _FILE_EXT:
        return relative
    parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
    return parent or None


def _ext(leaf: str) -> str:
    return leaf[leaf.rfind("."):].lower() if "." in leaf else ""


def _plan_files(listed: str, file_names: list[str] | None) -> list[FileRename]:
    """Rename the one file of each book type to the list name. Keep its folder."""
    if not file_names:
        return []
    by_ext: dict[str, list[str]] = {}
    for name in file_names:
        leaf = name.rsplit("/", 1)[-1]
        ext = _ext(leaf)
        if ext in _RENAME_EXT:
            by_ext.setdefault(ext, []).append(name)
    ops: list[FileRename] = []
    for ext, paths in by_ext.items():
        if len(paths) != 1:
            continue
        old = paths[0]
        suffix = old[old.rfind("."):]
        new_leaf = listed + suffix
        new = f"{old.rsplit('/', 1)[0]}/{new_leaf}" if "/" in old else new_leaf
        if new != old:
            ops.append(FileRename(old=old, new=new))
    return ops


def plan_listed_rename(listed: str, content_path: str, save_path: str,
                       taken_names: set[str], files: list[str] | None = None) -> ListedNamePlan:
    """Decide folder and single-file renames. `taken_names` are folders at save_path."""
    if not (content_path or "").strip():
        return ListedNamePlan(skipped=True, note="no content path")
    relative = _relative(content_path, save_path)
    if not relative:
        return ListedNamePlan(skipped=True, note="no content path")
    if _unsafe(listed):
        return ListedNamePlan(skipped=True, note="listed name is not a folder name")
    folder = _folder_to_rename(relative) or ""
    file_ops = _plan_files(listed, files)
    folder_old = ""
    folder_new = ""
    note = ""
    if folder and folder != listed:
        if listed in taken_names and listed != folder:
            note = "target folder already exists"
        else:
            folder_old, folder_new = folder, listed
    elif folder == listed and not file_ops:
        note = "already matches"
    if not folder_old and not file_ops:
        return ListedNamePlan(
            skipped=True,
            note=note or ("no folder to rename" if not folder else "already matches"),
        )
    return ListedNamePlan(skipped=False, old=folder_old, new=folder_new, files=file_ops)


@dataclass
class _TorrentPlan:
    hash: str
    name: str
    plan: ListedNamePlan = field(default_factory=lambda: ListedNamePlan(skipped=True))


def _torrent_files(client, torrent_hash: str) -> list[str] | None:
    getter = getattr(client, "files", None)
    if getter is None:
        return None
    try:
        raw = getter(torrent_hash)
    except Exception as exc:  # noqa: BLE001 — a missing file list must not stop the poll
        log.warning("file list failed for %s: %s", torrent_hash, exc)
        return None
    names = []
    for item in raw or []:
        if isinstance(item, dict):
            names.append(item.get("name") or "")
        else:
            names.append(getattr(item, "name", "") or "")
    return [name for name in names if name]


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
                files=_torrent_files(client, t.get("hash") or ""),
            )
            if plan.new:
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
        err = ""
        # Rename the file first, while its current path still exists, then the folder.
        for fop in plan.files:
            log.info("%srenameFile: '%s' -> '%s'",
                     "[dry-run] " if preview else "", fop.old, fop.new)
            if not preview:
                try:
                    client.rename_file(item.hash, fop.old, fop.new)
                except Exception as exc:  # noqa: BLE001
                    err = str(exc)
                    log.error("  failed: %s", exc)
                    break
        if not err and plan.old and plan.new and plan.old != plan.new:
            log.info("%srenameFolder: '%s' -> '%s'",
                     "[dry-run] " if preview else "", plan.old, plan.new)
            if not preview:
                try:
                    client.rename_folder(item.hash, plan.old, plan.new)
                except Exception as exc:  # noqa: BLE001
                    err = str(exc)
                    log.error("  failed: %s", exc)
        results.append({
            "name": item.name, "hash": item.hash, "skipped": False,
            "applied": (not preview) and not err,
            "note": err or ("preview" if preview else ""),
            "old": plan.old, "new": plan.new,
            "files": [{"old": f.old, "new": f.new} for f in plan.files],
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
