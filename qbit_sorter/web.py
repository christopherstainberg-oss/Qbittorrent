"""FastAPI web UI for qBittorrent Auto-Sorter.

Serves a single self-contained page that lets you:
  * see completed/seeding torrents with their current + proposed category,
  * run your rules (dry-run preview or apply for real),
  * manually assign a category to selected torrents (interactive sort).

Everything reuses the same config / client / sorter logic as the CLI.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import audiobooks as ab_mod
from . import config_store
from .client import QbitClient
from .config import (Config, ConfigError, load_config, rule_to_dict,
                     validate_rules)
from .rules import TorrentView, match_torrent
from .scheduler import PipelineRunner
from .sorter import Plan, apply_plan, build_plan

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class SetCategoryBody(BaseModel):
    hashes: list[str]
    category: str


class RunBody(BaseModel):
    dry_run: bool = True


class RulesBody(BaseModel):
    rules: list[dict]


class SettingsBody(BaseModel):
    dry_run: bool | None = None
    poll_enabled: bool | None = None
    poll_interval_minutes: float | None = None


class ArrBody(BaseModel):
    sonarr: dict | None = None
    radarr: dict | None = None


class CategoryBody(BaseModel):
    name: str
    save_path: str = ""


class _State:
    """Holds config + a lazily-connected client for the app's lifetime."""

    def __init__(self, config_path: str | Path):
        self.config_path = str(config_path)
        self.cfg: Config = load_config(config_path)
        self._client: QbitClient | None = None

    def client(self) -> QbitClient:
        if self._client is None:
            c = QbitClient(self.cfg.qbittorrent)
            c.connect()
            self._client = c
        return self._client

    def reload_config(self) -> None:
        self.cfg = load_config(self.config_path)


def _proposed(cfg: Config, t: TorrentView) -> tuple[str | None, str]:
    """Return (proposed_category, reason) for a torrent, or (None, '')."""
    rule = match_torrent(cfg.rules, t)
    if rule:
        return rule.category, f"rule '{rule.name}'"
    if cfg.default_category:
        return cfg.default_category, "default_category"
    return None, ""


def create_app(config_path: str | Path = "config.yaml") -> FastAPI:
    state = _State(config_path)
    runner = PipelineRunner(state)
    webhook_token = os.getenv("WEBHOOK_TOKEN", "")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        runner.start()
        try:
            yield
        finally:
            await runner.stop()

    app = FastAPI(title="qBittorrent Auto-Sorter", lifespan=lifespan)

    # Allow the UI to be hosted elsewhere (e.g. Cloudflare Pages) and call this
    # backend cross-origin through a tunnel. Default '*' for easy self-hosting;
    # set CORS_ORIGINS to a comma-separated allowlist to lock it down.
    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _err(exc: Exception) -> HTTPException:
        return HTTPException(status_code=502, detail=str(exc))

    def _reload() -> None:
        state.reload_config()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/info")
    def info() -> dict[str, Any]:
        c = state.client()._client  # underlying qbittorrentapi client
        try:
            return {
                "host": state.cfg.qbittorrent.host,
                "version": c.app.version,
                "api_version": c.app.web_api_version,
                "states": state.cfg.states,
                "dry_run_default": state.cfg.dry_run,
                "enable_autotmm": state.cfg.enable_autotmm,
                "audiobooks_enabled": state.cfg.audiobooks.enabled,
                "audiobooks_category": state.cfg.audiobooks.category,
                "poll": {"enabled": state.cfg.poll.enabled,
                         "interval_minutes": state.cfg.poll.interval_minutes},
                "arr": [{"name": s.name, "enabled": s.enabled, "url": s.url,
                         "category": s.category, "command": s.command}
                        for s in state.cfg.arr],
                "dry_run": state.cfg.dry_run,
                "rules": [{"name": r.name, "category": r.category} for r in state.cfg.rules],
            }
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)

    @app.get("/api/categories")
    def categories() -> list[dict[str, str]]:
        try:
            cats = state.client().categories()
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        out = []
        for name, meta in sorted(cats.items()):
            save_path = meta.get("savePath", "") if isinstance(meta, dict) else ""
            out.append({"name": name, "save_path": save_path})
        return out

    @app.get("/api/torrents")
    def torrents() -> list[dict[str, Any]]:
        try:
            raw = state.client().torrents(state.cfg.states)
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        views = [TorrentView.from_api(t) for t in raw]
        out = []
        for t in sorted(views, key=lambda x: x.name.lower()):
            proposed, reason = _proposed(state.cfg, t)
            out.append({
                "hash": t.hash,
                "name": t.name,
                "category": t.category,
                "proposed": proposed,
                "reason": reason,
                "size": t.size,
                "state": t.state,
                "save_path": t.save_path,
                "changes": bool(proposed) and proposed != t.category,
            })
        return out

    @app.post("/api/run")
    def run_rules(body: RunBody) -> dict[str, Any]:
        try:
            client = state.client()
            raw = client.torrents(state.cfg.states)
            views = [TorrentView.from_api(t) for t in raw]
            plans: list[Plan] = build_plan(state.cfg, views)
            # Honor the request's dry_run flag without mutating shared config.
            saved = state.cfg.dry_run
            state.cfg.dry_run = body.dry_run
            try:
                results = apply_plan(state.cfg, client, plans)
            finally:
                state.cfg.dry_run = saved
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        return {"dry_run": body.dry_run, "count": len(results), "results": results}

    @app.post("/api/pipeline/run")
    async def pipeline_run() -> dict[str, Any]:
        """Run the full pipeline once (categorize -> audiobooks -> *arr),
        honoring the configured dry_run setting."""
        try:
            return await runner.run_once(source="manual")
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)

    @app.post("/api/hooks/complete")
    async def hook_complete(request: Request, hash: str | None = None,
                            token: str | None = None) -> dict[str, Any]:
        """Webhook for qBittorrent's 'run external program on completion'.
        e.g. curl -X POST 'http://host:8500/api/hooks/complete?hash=%I'
        Runs the full (idempotent) pipeline. Optional WEBHOOK_TOKEN guards it."""
        if webhook_token and token != webhook_token:
            raise HTTPException(status_code=403, detail="Invalid or missing token.")
        log.info("Completion webhook received%s", f" for {hash}" if hash else "")
        try:
            summary = await runner.run_once(source="webhook")
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        return {"ok": True, "hash": hash, "summary": summary}

    @app.post("/api/audiobooks/run")
    def audiobooks_run(body: RunBody) -> dict[str, Any]:
        if not state.cfg.audiobooks.enabled:
            raise HTTPException(status_code=400,
                                detail="Audiobook organizing is disabled (audiobooks.enabled).")
        try:
            client = state.client()
            plans = ab_mod.build_plans(state.cfg, client)
            saved = state.cfg.dry_run
            state.cfg.dry_run = body.dry_run
            try:
                results = ab_mod.apply_plans(state.cfg, client, plans)
            finally:
                state.cfg.dry_run = saved
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        changed = [r for r in results if not r["skipped"]]
        return {"dry_run": body.dry_run, "category": state.cfg.audiobooks.category,
                "count": len(changed), "results": results}

    @app.post("/api/set-category")
    def set_category(body: SetCategoryBody) -> dict[str, Any]:
        if not body.hashes:
            raise HTTPException(status_code=400, detail="No torrents selected.")
        if not body.category:
            raise HTTPException(status_code=400, detail="No category given.")
        try:
            client = state.client()
            cats = client.categories()
            if body.category not in cats:
                if state.cfg.create_missing_categories:
                    client.create_category(body.category)
                    save_path = ""
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Category '{body.category}' does not exist. Create it "
                               "in qBittorrent, or set create_missing_categories: true.",
                    )
            else:
                meta = cats[body.category]
                save_path = meta.get("savePath", "") if isinstance(meta, dict) else ""
            client.set_category(body.category, body.hashes)
            # Only relocate when the category has a real save path (see sorter).
            relocate = state.cfg.enable_autotmm and bool(save_path)
            if relocate:
                client.enable_autotmm(body.hashes)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        return {"ok": True, "category": body.category, "count": len(body.hashes),
                "relocated": relocate}

    # ---- Editing: rules / settings / arr / category placement --------------

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        """Full editable config for the UI editors."""
        return {
            "rules": [rule_to_dict(r) for r in state.cfg.rules],
            "default_category": state.cfg.default_category,
            "settings": {
                "dry_run": state.cfg.dry_run,
                "enable_autotmm": state.cfg.enable_autotmm,
                "create_missing_categories": state.cfg.create_missing_categories,
                "poll_enabled": state.cfg.poll.enabled,
                "poll_interval_minutes": state.cfg.poll.interval_minutes,
            },
            "arr": [{"name": s.name, "enabled": s.enabled, "url": s.url,
                     "api_key": bool(s.api_key), "category": s.category,
                     "command": s.command} for s in state.cfg.arr],
        }

    @app.put("/api/rules")
    def put_rules(body: RulesBody) -> dict[str, Any]:
        try:
            cleaned = validate_rules(body.rules)          # validates regex etc.
            config_store.save_rules(state.config_path, cleaned)
            _reload()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "rules": [rule_to_dict(r) for r in state.cfg.rules]}

    @app.put("/api/settings")
    def put_settings(body: SettingsBody) -> dict[str, Any]:
        poll: dict[str, Any] = {}
        if body.poll_enabled is not None:
            poll["enabled"] = body.poll_enabled
        if body.poll_interval_minutes is not None:
            if body.poll_interval_minutes <= 0:
                raise HTTPException(status_code=400,
                                    detail="poll_interval_minutes must be > 0.")
            poll["interval_minutes"] = body.poll_interval_minutes
        try:
            config_store.save_settings(state.config_path,
                                       dry_run=body.dry_run,
                                       poll=poll or None)
            _reload()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "settings": {
            "dry_run": state.cfg.dry_run,
            "poll_enabled": state.cfg.poll.enabled,
            "poll_interval_minutes": state.cfg.poll.interval_minutes,
        }}

    @app.put("/api/arr")
    def put_arr(body: ArrBody) -> dict[str, Any]:
        services: dict[str, dict] = {}
        if body.sonarr is not None:
            services["sonarr"] = body.sonarr
        if body.radarr is not None:
            services["radarr"] = body.radarr
        if not services:
            raise HTTPException(status_code=400, detail="No arr settings provided.")
        try:
            config_store.save_arr(state.config_path, services)
            _reload()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True}

    @app.post("/api/categories")
    def create_category(body: CategoryBody) -> dict[str, Any]:
        if not body.name:
            raise HTTPException(status_code=400, detail="Category name required.")
        try:
            state.client().create_category(body.name, body.save_path)
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        return {"ok": True, "name": body.name, "save_path": body.save_path}

    @app.put("/api/categories")
    def edit_category(body: CategoryBody) -> dict[str, Any]:
        if not body.name:
            raise HTTPException(status_code=400, detail="Category name required.")
        try:
            state.client().edit_category(body.name, body.save_path)
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        return {"ok": True, "name": body.name, "save_path": body.save_path}

    @app.post("/api/reload")
    def reload_config() -> dict[str, Any]:
        try:
            state.reload_config()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "rules": len(state.cfg.rules)}

    return app

