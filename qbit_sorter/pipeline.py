"""One orchestrated pass: categorize -> organize audiobooks -> notify *arr.

Shared by the CLI (`--all`), the completion webhook and the poll loop. Every
step is idempotent, so running it repeatedly (or on every completion) is safe.
Respects `cfg.dry_run`.
"""

from __future__ import annotations

import logging
from typing import Any

from . import arr as arr_mod
from . import audiobooks as ab_mod
from . import sorter
from .client import QbitClient
from .config import Config

log = logging.getLogger(__name__)


def run_pipeline(cfg: Config, client: QbitClient,
                 only_categories: set[str] | None = None) -> dict[str, Any]:
    """Run the full pipeline once and return a combined summary."""
    summary: dict[str, Any] = {"dry_run": cfg.dry_run}

    summary["sorted"] = sorter.run(cfg, client)

    if cfg.audiobooks.enabled:
        summary["audiobooks"] = ab_mod.organize(cfg, client)
    else:
        summary["audiobooks"] = []

    summary["arr"] = arr_mod.trigger_for_completed(
        cfg, client, only_categories=only_categories)

    changed = sum(1 for r in summary["sorted"] if not r.get("skipped")) \
        + sum(1 for r in summary["audiobooks"] if not r.get("skipped"))
    triggered = sum(1 for r in summary["arr"] if r.get("triggered"))
    log.info("Pipeline done: %d torrent change(s), %d *arr trigger(s)%s.",
             changed, triggered, " [dry-run]" if cfg.dry_run else "")
    return summary
