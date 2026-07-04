"""Organize completed audiobook torrents via qBittorrent's rename API.

For each completed torrent in the audiobook category we parse its qBittorrent
name as ``Title <delimiter> Author`` and normalize the on-disk layout:

* single-file torrent  -> ``<Author>/<Title>/<Title>.<ext>``  (folder created,
  file renamed)
* multi-file torrent    -> the content folder is renamed to ``<Author>/<Title>``
  (inner files are left untouched)

All moves are performed through qBittorrent (``renameFile`` / ``renameFolder``)
so they work on the machine that actually holds the data (e.g. a NAS), and are
reflected in qBittorrent's own bookkeeping.
"""

from __future__ import annotations

import logging
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

from .client import QbitClient
from .config import AudiobookConfig, Config

log = logging.getLogger(__name__)

# Characters that are illegal (or troublesome) in Windows / SMB filenames.
_ILLEGAL = {
    ":": " -",
    "/": "-",
    "\\": "-",
    "<": "",
    ">": "",
    '"': "",
    "|": "",
    "?": "",
    "*": "",
}
_WS = re.compile(r"\s+")


def sanitize_component(name: str) -> str:
    """Make a single path component safe for Windows/SMB filesystems."""
    for bad, good in _ILLEGAL.items():
        name = name.replace(bad, good)
    name = _WS.sub(" ", name).strip()
    # Windows dislikes trailing dots/spaces on a name component.
    name = name.rstrip(" .")
    return name


def parse_title_author(name: str, delimiter: str) -> tuple[str, str] | None:
    """Split a torrent name into (title, author) on the LAST delimiter.

    Returns None when the delimiter is not present.
    """
    idx = name.rfind(delimiter)
    if idx == -1:
        return None
    title = name[:idx].strip()
    author = name[idx + len(delimiter):].strip()
    if not title or not author:
        return None
    return title, author


@dataclass
class Op:
    """A single qBittorrent rename call."""

    method: str  # "rename_file" | "rename_folder"
    old: str
    new: str


@dataclass
class AudiobookPlan:
    hash: str
    name: str
    title: str = ""
    author: str = ""
    ops: list[Op] = field(default_factory=list)
    skipped: bool = False
    note: str = ""


def _split_ext(path: str) -> str:
    return posixpath.splitext(path)[1]


def _top_container(paths: list[str]) -> str | None:
    """Return the single top-level folder shared by every path, else None
    (None means at least one file sits directly at the torrent root, or the
    files live under more than one top-level folder)."""
    if any("/" not in p for p in paths):
        return None
    roots = {p.split("/", 1)[0] for p in paths}
    return roots.pop() if len(roots) == 1 else None


def plan_for_torrent(ab: AudiobookConfig, t: Any, files: list[Any]) -> AudiobookPlan:
    """Build the rename plan for one torrent (no side effects)."""
    name = t.get("name", "") if isinstance(t, dict) else getattr(t, "name", "")
    thash = t.get("hash", "") if isinstance(t, dict) else getattr(t, "hash", "")
    plan = AudiobookPlan(hash=thash, name=name)

    parsed = parse_title_author(name, ab.delimiter)
    if not parsed:
        plan.skipped = True
        plan.note = f"name not in 'Title{ab.delimiter}Author' format"
        return plan
    title, author = parsed
    if ab.sanitize:
        title, author = sanitize_component(title), sanitize_component(author)
    plan.title, plan.author = title, author

    fields = {"title": title, "author": author}
    target_folder = ab.folder_template.format(**fields).strip("/")
    file_stem = ab.file_template.format(**fields)

    paths = [f.get("name", "") if isinstance(f, dict) else f.name for f in files]
    paths = [p for p in paths if p]
    if not paths:
        plan.skipped = True
        plan.note = "torrent reports no files yet"
        return plan

    if len(paths) == 1:
        # Single file -> <target_folder>/<file_stem><ext>
        old = paths[0]
        new = f"{target_folder}/{file_stem}{_split_ext(old)}"
        if old == new:
            plan.skipped = True
            plan.note = "already organized"
        else:
            plan.ops.append(Op("rename_file", old, new))
        return plan

    # Multi-file torrent. If every file already lives under the target folder,
    # there is nothing to do (idempotent — handles names whose target itself is
    # nested, e.g. 'Author/Title').
    prefix = target_folder + "/"
    if all(p.startswith(prefix) for p in paths):
        plan.skipped = True
        plan.note = "already organized"
        return plan

    root = _top_container(paths)
    if root is not None:
        # Rename the single top-level content folder to the target.
        plan.ops.append(Op("rename_folder", root, target_folder))
    else:
        # No single container folder: move each file under target_folder,
        # preserving any relative structure.
        for p in paths:
            plan.ops.append(Op("rename_file", p, f"{target_folder}/{p}"))
    return plan


def build_plans(cfg: Config, client: QbitClient) -> list[AudiobookPlan]:
    ab = cfg.audiobooks
    plans: list[AudiobookPlan] = []
    torrents = client.completed_in_category(ab.category)
    log.info("Found %d completed torrent(s) in category '%s'.", len(torrents), ab.category)
    for t in torrents:
        thash = t.get("hash", "") if isinstance(t, dict) else t.hash
        files = client.files(thash)
        plans.append(plan_for_torrent(ab, t, files))
    return plans


def apply_plans(cfg: Config, client: QbitClient, plans: list[AudiobookPlan]) -> list[dict]:
    """Execute (or, in dry-run, describe) the rename plans. Returns result dicts."""
    results: list[dict] = []
    for plan in plans:
        target = f"{plan.author}/{plan.title}" if plan.author else ""
        if plan.skipped or not plan.ops:
            log.info("skip: %s%s", plan.name, f"  ({plan.note})" if plan.note else "")
            results.append({
                "name": plan.name, "hash": plan.hash, "target": target,
                "applied": False, "skipped": True,
                "note": plan.note or "nothing to change", "ops": [],
            })
            continue

        op_descs = []
        ok = True
        err = ""
        for op in plan.ops:
            log.info("%s%s: '%s' -> '%s'",
                     "[dry-run] " if cfg.dry_run else "", op.method, op.old, op.new)
            op_descs.append({"method": op.method, "old": op.old, "new": op.new})
            if not cfg.dry_run:
                try:
                    getattr(client, op.method)(plan.hash, op.old, op.new)
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    err = str(exc)
                    log.error("  failed: %s", exc)
                    break
        results.append({
            "name": plan.name, "hash": plan.hash, "target": target,
            "applied": (not cfg.dry_run) and ok, "skipped": False,
            "note": err if err else ("" if not cfg.dry_run else "preview"),
            "ops": op_descs,
        })
    verb = "Would organize" if cfg.dry_run else "Organized"
    changed = sum(1 for r in results if not r["skipped"])
    log.info("%s %d audiobook torrent(s).", verb, changed)
    return results


def organize(cfg: Config, client: QbitClient) -> list[dict]:
    """Plan and apply audiobook organization. Returns per-torrent result dicts."""
    if not cfg.audiobooks.enabled:
        log.warning("Audiobook organizing is disabled (set audiobooks.enabled: true).")
        return []
    plans = build_plans(cfg, client)
    return apply_plans(cfg, client, plans)
