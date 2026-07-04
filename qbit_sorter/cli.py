"""Command-line interface for qBittorrent Auto-Sorter."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .client import QbitClient
from .config import Config, ConfigError, load_config
from .rules import TorrentView, match_torrent
from .sorter import build_plan, run


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def _cmd_list_categories(client: QbitClient) -> int:
    cats = client.categories()
    if not cats:
        print("No categories are configured in qBittorrent.")
        return 0
    print(f"{'CATEGORY':<24} SAVE PATH")
    print("-" * 60)
    for name, info in sorted(cats.items()):
        save_path = info.get("savePath", "") if isinstance(info, dict) else ""
        print(f"{name:<24} {save_path}")
    return 0


def _cmd_list_torrents(cfg: Config, client: QbitClient) -> int:
    raw = client.torrents(cfg.states)
    torrents = [TorrentView.from_api(t) for t in raw]
    if not torrents:
        print("No torrents match the configured states.")
        return 0
    print(f"{'CURRENT':<14} {'-> PROPOSED':<16} NAME")
    print("-" * 78)
    for t in sorted(torrents, key=lambda x: x.name.lower()):
        rule = match_torrent(cfg.rules, t)
        if rule:
            proposed = rule.category
        elif cfg.default_category:
            proposed = cfg.default_category
        else:
            proposed = "(no match)"
        current = t.category or "(none)"
        marker = "  " if current == proposed else "* "
        print(f"{marker}{current:<12} {proposed:<16} {t.name}")
    print("\n(* = would change)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qbit-sorter",
        description="Auto-categorize completed/seeding qBittorrent torrents and "
                    "relocate their data into each category's save path.",
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Force dry-run: show changes without applying (overrides config).",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Force real run: apply changes even if config sets dry_run: true.",
    )
    parser.add_argument(
        "--list-categories", action="store_true",
        help="List qBittorrent categories and their save paths, then exit.",
    )
    parser.add_argument(
        "--list-torrents", action="store_true",
        help="Show each torrent and the category it would get, then exit.",
    )
    parser.add_argument(
        "--organize-audiobooks", action="store_true",
        help="Organize completed audiobook-category torrents into "
             "<Author>/<Title> (respects dry-run) instead of running rules.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Run the full pipeline: categorize -> organize audiobooks -> "
             "notify Sonarr/Radarr.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose (debug) logging.",
    )
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("qbit-sorter")

    if args.dry_run and args.apply:
        log.error("--dry-run and --apply are mutually exclusive.")
        return 2

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    if args.dry_run:
        cfg.dry_run = True
    if args.apply:
        cfg.dry_run = False

    client = QbitClient(cfg.qbittorrent)
    try:
        client.connect()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    if args.list_categories:
        return _cmd_list_categories(client)
    if args.list_torrents:
        return _cmd_list_torrents(cfg, client)

    if cfg.dry_run:
        log.info("DRY RUN — no changes will be made. Use --apply (or set "
                 "dry_run: false) to act for real.")
    try:
        if args.all:
            from .pipeline import run_pipeline
            run_pipeline(cfg, client)
        elif args.organize_audiobooks:
            from .audiobooks import organize
            if not cfg.audiobooks.enabled:
                log.error("audiobooks.enabled is false in the config.")
                return 2
            organize(cfg, client)
        else:
            run(cfg, client)
    except Exception as exc:  # noqa: BLE001 - surface a clean message to the user
        log.error("Run failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
