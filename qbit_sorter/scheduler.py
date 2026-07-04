"""Serialize pipeline runs and drive the periodic poll loop.

A single :class:`PipelineRunner` guards all executions with an asyncio lock so
the webhook and the poll loop never run the pipeline concurrently against the
same qBittorrent client. The blocking pipeline runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .pipeline import run_pipeline

log = logging.getLogger(__name__)


class PipelineRunner:
    def __init__(self, state: Any):
        # `state` is duck-typed: needs .cfg and .client() (see web._State).
        self._state = state
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    async def run_once(self, source: str = "manual",
                       only_categories: set[str] | None = None) -> dict[str, Any]:
        async with self._lock:
            log.info("Pipeline run (%s)…", source)
            client = self._state.client()
            return await asyncio.to_thread(
                run_pipeline, self._state.cfg, client, only_categories)

    async def _loop(self) -> None:
        # Small initial delay so startup finishes before the first poll.
        await asyncio.sleep(5.0)
        while True:
            cfg = self._state.cfg  # re-read each iteration so UI edits apply live
            if not cfg.poll.enabled:
                # Disabled: check again soon so toggling it on is responsive.
                await asyncio.sleep(15.0)
                continue
            try:
                await self.run_once(source="poll")
            except Exception as exc:  # noqa: BLE001
                log.error("Poll run failed: %s", exc)
            await asyncio.sleep(max(0.25, cfg.poll.interval_minutes) * 60.0)

    def start(self) -> None:
        """Always start the loop task; it self-gates on poll.enabled so the
        web UI can turn polling on/off and change the interval at runtime."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            log.info("Scheduler started (poll.enabled=%s, every %.2f min).",
                     self._state.cfg.poll.enabled,
                     self._state.cfg.poll.interval_minutes)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
