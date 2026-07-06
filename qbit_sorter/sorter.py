"""Core logic: decide each torrent's category and apply the changes."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from .client import QbitClient
from .config import Config
from .rules import TorrentView, match_torrent

log = logging.getLogger(__name__)


@dataclass
class Plan:
    """A single intended change: move `torrent` into `category` per `reason`."""

    torrent: TorrentView
    category: str
    reason: str


def build_plan(cfg: Config, torrents: list[TorrentView]) -> list[Plan]:
    """Work out, for each torrent, which category it should have.

    Skips torrents already in the right category so we don't issue redundant
    moves. Returns only the torrents that need changing.
    """
    plans: list[Plan] = []
    for t in torrents:
        rule = match_torrent(cfg.rules, t)
        if rule is not None:
            target, reason = rule.category, f"rule '{rule.name}'"
        elif cfg.default_category:
            target, reason = cfg.default_category, "default_category"
        else:
            log.debug("No rule matched '%s' — leaving untouched", t.name)
            continue

        if t.category == target:
            log.debug("'%s' already in category '%s'", t.name, target)
            continue

        plans.append(Plan(torrent=t, category=target, reason=reason))
    return plans


def _category_paths(client: QbitClient) -> dict[str, str]:
    """Return {category_name: save_path} for every existing category."""
    out: dict[str, str] = {}
    for name, meta in client.categories().items():
        out[name] = meta.get("savePath", "") if isinstance(meta, dict) else ""
    return out


def _ensure_categories(cfg: Config, client: QbitClient, plans: list[Plan]) -> dict[str, str]:
    """Make sure every target category exists. Returns {category: save_path}
    for usable categories; drops (with a warning) any missing category we
    can't/won't create."""
    paths = _category_paths(client)
    existing = set(paths)
    wanted = {p.category for p in plans}

    usable = dict(paths)
    for cat in wanted - existing:
        if cfg.create_missing_categories:
            # Find a save_path hint from the first rule targeting this category.
            hint = next(
                (r.save_path for r in cfg.rules if r.category == cat and r.save_path),
                None,
            )
            if cfg.dry_run:
                log.info("[dry-run] would create category '%s'%s", cat,
                         f" (save_path={hint})" if hint else "")
            else:
                client.create_category(cat, hint)
                log.info("Created category '%s'%s", cat,
                         f" (save_path={hint})" if hint else "")
            usable[cat] = hint or ""
        else:
            log.warning(
                "Category '%s' does not exist in qBittorrent and "
                "create_missing_categories is false — skipping those torrents.",
                cat,
            )
    return usable


def apply_plan(cfg: Config, client: QbitClient, plans: list[Plan]) -> list[dict]:
    """Apply the plan against qBittorrent.

    Returns one result dict per torrent: {name, hash, category, reason,
    applied, skipped, note}. In dry-run mode nothing is changed but the
    intended actions are still reported (applied=False).
    """
    if not plans:
        log.info("Nothing to do — all torrents already sorted.")
        return []

    usable = _ensure_categories(cfg, client, plans)  # {category: save_path}

    results: list[dict] = []
    actionable: list[Plan] = []
    for p in plans:
        if p.category in usable:
            actionable.append(p)
        else:
            results.append({
                "name": p.torrent.name, "hash": p.torrent.hash,
                "category": p.category, "reason": p.reason,
                "applied": False, "skipped": True,
                "note": f"category '{p.category}' missing",
            })

    # Batch by target category so we issue one API call per category.
    by_category: dict[str, list[Plan]] = defaultdict(list)
    for p in actionable:
        by_category[p.category].append(p)

    # Auto-organize moves each torrent into its category's folder (the
    # category's save path, or <default>/<category> when it has none), so every
    # category — not just ones with an explicit path — lands in a folder. Only
    # complete torrents are moved; active downloads stay put.
    default_path = client.default_save_path() if (cfg.enable_autotmm and not cfg.dry_run) else ""
    moved = 0
    for category, group in by_category.items():
        hashes = [p.torrent.hash for p in group]
        relocate = cfg.enable_autotmm
        note = "" if cfg.enable_autotmm else "auto-organize off — category set only, files not moved"
        for p in group:
            log.info(
                "%s'%s'  ->  category '%s'  (%s)",
                "[dry-run] " if cfg.dry_run else "",
                p.torrent.name, category, p.reason,
            )
        if not cfg.dry_run:
            client.set_category(category, hashes)
            if relocate:
                try:
                    n_moved, n_skip, dest = client.organize(
                        category, usable.get(category, ""), default_path, hashes)
                    relocate = n_moved > 0
                    note = f"moved {n_moved} into {dest}" + (
                        f", {n_skip} still downloading (not moved)" if n_skip else "")
                except Exception as exc:  # noqa: BLE001 — unwritable save path etc.
                    relocate = False
                    note = f"relocation failed — {exc}"
                    log.warning("Relocation for category '%s' failed: %s", category, exc)
        for p in group:
            results.append({
                "name": p.torrent.name, "hash": p.torrent.hash,
                "category": category, "reason": p.reason,
                "applied": not cfg.dry_run, "skipped": False,
                "relocated": relocate, "note": note,
            })
        moved += len(hashes)

    verb = "Would move" if cfg.dry_run else "Moved"
    log.info("%s %d torrent(s) across %d category(ies).", verb, moved, len(by_category))
    return results


def run(cfg: Config, client: QbitClient) -> list[dict]:
    """Fetch, plan and apply. Returns per-torrent result dicts."""
    raw = client.torrents(cfg.states)
    torrents = [TorrentView.from_api(t) for t in raw]
    log.info("Fetched %d torrent(s) in states: %s", len(torrents), ", ".join(cfg.states))
    plans = build_plan(cfg, torrents)
    return apply_plan(cfg, client, plans)
