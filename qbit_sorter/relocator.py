"""Sonarr/Radarr-style library relocation.

Moves the data of **completed** torrents in configured categories out to an
external library path that qBittorrent itself can't reach — the app does the
transfer through its own mounts. Default mode hardlinks each file (instant, no
extra space, seeding keeps working) and falls back to a copy across filesystems.

Path model: qBittorrent reports each torrent's files under `content_path`,
rooted at `qbit_download_root` (e.g. /Torrents). The same storage is mounted
into this app at `local_download_root`, so the readable source is
content_path with the root swapped. The destination is `<Destination.path>/<name>`.
"""

from __future__ import annotations

import logging
import os
import shutil

from .client import QbitClient
from .config import Config

log = logging.getLogger(__name__)


def local_source(content_path: str, qbit_root: str, local_root: str) -> str:
    """Translate a qBittorrent content_path into this app's filesystem view."""
    qbit_root = (qbit_root or "").rstrip("/")
    if qbit_root and local_root and content_path.startswith(qbit_root):
        rel = content_path[len(qbit_root):].lstrip("/")
        return os.path.join(local_root, *rel.split("/")) if rel else local_root
    return content_path  # roots not configured / already local: assume same path


def _transfer_file(src: str, dst: str, mode: str) -> str:
    """Transfer one file. Returns the action taken."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        return "exists"          # idempotent: never overwrite an existing file
    if mode == "copy":
        shutil.copy2(src, dst)
        return "copy"
    if mode == "move":
        shutil.move(src, dst)
        return "move"
    # hardlink, else copy (cross-filesystem)
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def transfer(src: str, dst: str, mode: str) -> list[dict]:
    """Transfer a file or a whole directory tree. Returns per-file ops."""
    ops: list[dict] = []
    if os.path.isdir(src):
        for root, _dirs, files in os.walk(src):
            rel = os.path.relpath(root, src)
            for name in files:
                s = os.path.join(root, name)
                d = os.path.join(dst, name) if rel == "." else os.path.join(dst, rel, name)
                ops.append({"action": _transfer_file(s, d, mode), "dst": d})
    else:
        ops.append({"action": _transfer_file(src, dst, mode), "dst": dst})
    return ops


def build_plans(cfg: Config, client: QbitClient) -> list[dict]:
    """One entry per completed torrent in a destination category."""
    rc = cfg.relocation
    plans: list[dict] = []
    for dest in rc.destinations:
        for t in client.completed_in_category(dest.category):
            content = (t.get("content_path", "") or "").rstrip("/")
            src = local_source(content, rc.qbit_download_root, rc.local_download_root)
            target = os.path.join(dest.path, os.path.basename(content)) if content else ""
            plans.append({
                "hash": t.get("hash", ""),
                "name": t.get("name", ""),
                "category": dest.category,
                "mode": dest.mode,
                "content_path": content,
                "src": src,
                "dst": target,
            })
    return plans


def run(cfg: Config, client: QbitClient, dry_run: bool = True) -> list[dict]:
    """Relocate completed torrents in destination categories. In dry-run mode
    nothing is transferred but each intended action is reported."""
    results: list[dict] = []
    for p in build_plans(cfg, client):
        base = {"name": p["name"], "hash": p["hash"], "category": p["category"],
                "mode": p["mode"], "src": p["src"], "dst": p["dst"]}
        if not p["content_path"]:
            results.append({**base, "skipped": True, "note": "torrent has no content yet"})
            continue
        if not os.path.exists(p["src"]):
            results.append({**base, "skipped": True,
                            "note": "source not found — check mounts / download roots"})
            continue
        if os.path.exists(p["dst"]):
            results.append({**base, "skipped": True, "note": "already at destination"})
            continue
        if dry_run:
            results.append({**base, "skipped": False, "applied": False,
                            "note": f"would {p['mode']} → {p['dst']}"})
            continue
        try:
            ops = transfer(p["src"], p["dst"], p["mode"])
        except Exception as exc:  # noqa: BLE001 — report, don't abort the batch
            results.append({**base, "skipped": True, "note": f"failed — {exc}"})
            log.warning("Relocation of '%s' failed: %s", p["name"], exc)
            continue
        actions = {o["action"] for o in ops}
        results.append({**base, "skipped": False, "applied": True,
                        "files": len(ops), "actions": sorted(actions), "ops": ops,
                        "note": f"{'/'.join(sorted(actions))} → {p['dst']}"})
        log.info("Relocated '%s' -> %s (%s)", p["name"], p["dst"], ",".join(sorted(actions)))
    return results
