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
from . import automations as auto_mod
from . import config_store
from . import naming
from . import relocator
from .client import QbitClient
from .config import (AUTOMATION_ACTIONS, VALID_STATES, Config, ConfigError,
                     automation_to_dict, load_config, relocation_to_dict,
                     rule_to_dict, validate_automations, validate_rules)
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


class AutomationsBody(BaseModel):
    automations: list[dict]


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


class SavePathBody(BaseModel):
    save_path: str


class SetLocationBody(BaseModel):
    hashes: list[str]
    location: str


class SetPriorityBody(BaseModel):
    hashes: list[str]
    action: str  # "top" | "up" | "down" | "bottom"


class QueueingBody(BaseModel):
    enabled: bool


class SetFilePriorityBody(BaseModel):
    hashes: list[str]
    priority: int  # 0 do-not-download, 1 normal, 6 high, 7 maximal


class RelocationBody(BaseModel):
    enabled: bool | None = None
    qbit_download_root: str | None = None
    local_download_root: str | None = None
    destinations: list[dict] | None = None


class NamePreviewBody(BaseModel):
    folder_template: str = ""
    file_template: str = ""
    sample: str = ""
    category: str = ""


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

    # Static PWA assets (manifest, service worker, icons). Served from the app
    # root so the service worker's scope covers the whole UI. Cloudflare Pages
    # serves these directly from the static dir; this route covers the
    # container/tunnel deployment.
    _STATIC_ASSETS = {
        "manifest.webmanifest": "application/manifest+json",
        "sw.js": "text/javascript",
        "favicon.svg": "image/svg+xml",
        "favicon-32.png": "image/png",
        "apple-touch-icon.png": "image/png",
        "icon-192.png": "image/png",
        "icon-512.png": "image/png",
        "icon-maskable-192.png": "image/png",
        "icon-maskable-512.png": "image/png",
    }

    @app.get("/{filename}")
    def static_asset(filename: str) -> FileResponse:
        media_type = _STATIC_ASSETS.get(filename)
        path = STATIC_DIR / filename
        if media_type is None or not path.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        # Let service-worker updates land promptly; browsers must revalidate it.
        headers = {"Cache-Control": "no-cache"} if filename == "sw.js" else {}
        return FileResponse(path, media_type=media_type, headers=headers)

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
                "default_save_path": c.app.preferences.get("save_path", "") or "",
                "audiobooks_enabled": state.cfg.audiobooks.enabled,
                "audiobooks_category": state.cfg.audiobooks.category,
                "poll": {"enabled": state.cfg.poll.enabled,
                         "interval_minutes": state.cfg.poll.interval_minutes},
                "arr": [{"name": s.name, "enabled": s.enabled, "url": s.url,
                         "category": s.category, "command": s.command}
                        for s in state.cfg.arr],
                "dry_run": state.cfg.dry_run,
                "queueing_enabled": bool(c.app.preferences.get("queueing_enabled", False)),
                "rules": [{"name": r.name, "category": r.category} for r in state.cfg.rules],
            }
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)

    @app.get("/api/categories")
    def categories() -> list[dict[str, str]]:
        try:
            client = state.client()
            cats = client.categories()
            default_path = client.default_save_path()
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        out = []
        for name, meta in sorted(cats.items()):
            save_path = meta.get("savePath", "") if isinstance(meta, dict) else ""
            # Where torrents in this category actually land — matches
            # client.organize(): the category's save path, or <default>/<name>.
            destination = save_path or (default_path.rstrip("/") + "/" + name)
            out.append({"name": name, "save_path": save_path,
                        "destination": destination})
        return out

    @app.get("/api/torrents")
    def torrents(states: str | None = None) -> list[dict[str, Any]]:
        # `states` (comma-separated) overrides the configured list for this
        # request only — lets the UI view the download queue (which lives in
        # states like "downloading") without changing the sorter's config.
        if states:
            want = [s.strip() for s in states.split(",") if s.strip()]
            bad = [s for s in want if s not in VALID_STATES]
            if bad:
                raise HTTPException(status_code=400,
                                    detail=f"Unknown state(s): {', '.join(bad)}")
        else:
            want = state.cfg.states
        try:
            raw = state.client().torrents(want)
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
                "priority": t.priority,
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

    @app.post("/api/relocate/run")
    def relocate_run(body: RunBody) -> dict[str, Any]:
        """Relocate completed torrents in destination categories to their
        external library paths (Sonarr/Radarr-style, app-side transfer)."""
        rc = state.cfg.relocation
        if not rc.destinations:
            raise HTTPException(
                status_code=400,
                detail="No library destinations configured (Automation → Library relocation).")
        if not rc.local_download_root:
            raise HTTPException(
                status_code=400,
                detail="Set the download roots first so the app can read source files.")
        try:
            results = relocator.run(state.cfg, state.client(), dry_run=body.dry_run)
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        changed = [r for r in results if not r.get("skipped")]
        return {"dry_run": body.dry_run, "count": len(changed), "results": results}

    @app.post("/api/relocate/preview-name")
    def preview_name(body: NamePreviewBody) -> dict[str, Any]:
        """Render the naming template against a sample name — for the live UI
        preview. Pure/offline; no qBittorrent connection needed."""
        sample = body.sample or "The.Martian.2015.1080p.BluRay.x265-RARBG"
        tokens = naming.parse(sample, body.category)
        posix = lambda p: p.replace(os.sep, "/")
        return {
            "tokens": tokens,
            "file": posix(naming.destination_subpath(
                body.folder_template, body.file_template, tokens, True, ".mkv", sample + ".mkv")),
            "folder": posix(naming.destination_subpath(
                body.folder_template, body.file_template, tokens, False, "", sample)),
        }

    @app.post("/api/automations/run")
    def automations_run(body: RunBody) -> dict[str, Any]:
        """Evaluate every enabled trigger->action automation and apply it."""
        if not any(a.enabled for a in state.cfg.automations):
            raise HTTPException(status_code=400,
                                detail="No enabled automations (Automations tab).")
        try:
            results = auto_mod.run(state.cfg, state.client(), dry_run=body.dry_run)
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        applied = sum(r.get("applied", 0) for r in results)
        return {"dry_run": body.dry_run, "applied": applied, "results": results}

    @app.get("/api/automation-actions")
    def automation_actions() -> dict[str, list[str]]:
        """Action types + their required params, so the UI can render the editor."""
        return {k: list(v) for k, v in AUTOMATION_ACTIONS.items()}

    @app.post("/api/set-category")
    def set_category(body: SetCategoryBody) -> dict[str, Any]:
        if not body.hashes:
            raise HTTPException(status_code=400, detail="No torrents selected.")
        # Empty category means "remove category" (uncategorize) — used by the
        # per-row "none" option in the torrents table.
        if body.category == "":
            try:
                state.client().set_category("", body.hashes)
            except Exception as exc:  # noqa: BLE001
                raise _err(exc)
            return {"ok": True, "category": "", "count": len(body.hashes),
                    "relocated": False}
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
            # Auto-organize into the category's folder (its save path, or
            # <default>/<category>). Only complete torrents are moved — active
            # downloads stay put. Explicit Set Location surfaces a bad path.
            relocated = False
            moved = skipped_incomplete = 0
            relocate_error = None
            if state.cfg.enable_autotmm:
                try:
                    default_path = client.default_save_path()
                    moved, skipped_incomplete, _dest = client.organize(
                        body.category, save_path, default_path, body.hashes)
                    relocated = moved > 0
                except Exception as exc:  # noqa: BLE001 — bad/unwritable save path
                    relocate_error = str(exc)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        return {"ok": True, "category": body.category, "count": len(body.hashes),
                "relocated": relocated, "moved": moved,
                "skipped_incomplete": skipped_incomplete, "relocate_error": relocate_error}

    # ---- Editing: rules / settings / arr / category placement --------------

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        """Full editable config for the UI editors."""
        return {
            "rules": [rule_to_dict(r) for r in state.cfg.rules],
            "automations": [automation_to_dict(a) for a in state.cfg.automations],
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
            "relocation": relocation_to_dict(state.cfg.relocation),
        }

    @app.put("/api/relocation")
    def put_relocation(body: RelocationBody) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if body.enabled is not None:
            payload["enabled"] = body.enabled
        if body.qbit_download_root is not None:
            payload["qbit_download_root"] = body.qbit_download_root.strip()
        if body.local_download_root is not None:
            payload["local_download_root"] = body.local_download_root.strip()
        if body.destinations is not None:
            payload["destinations"] = body.destinations
        try:
            config_store.save_relocation(state.config_path, payload)
            _reload()  # re-validates (mode values, required fields)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "relocation": relocation_to_dict(state.cfg.relocation)}

    @app.put("/api/rules")
    def put_rules(body: RulesBody) -> dict[str, Any]:
        try:
            cleaned = validate_rules(body.rules)          # validates regex etc.
            config_store.save_rules(state.config_path, cleaned)
            _reload()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "rules": [rule_to_dict(r) for r in state.cfg.rules]}

    @app.put("/api/automations")
    def put_automations(body: AutomationsBody) -> dict[str, Any]:
        try:
            cleaned = validate_automations(body.automations)  # validates actions/params
            config_store.save_automations(state.config_path, cleaned)
            _reload()
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True,
                "automations": [automation_to_dict(a) for a in state.cfg.automations]}

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

    @app.post("/api/set-location")
    def set_location(body: SetLocationBody) -> dict[str, Any]:
        if not body.hashes:
            raise HTTPException(status_code=400, detail="No torrents selected.")
        loc = body.location.strip()
        if not loc:
            raise HTTPException(status_code=400, detail="Location path required.")
        try:
            state.client().set_location(loc, body.hashes)
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        return {"ok": True, "location": loc, "count": len(body.hashes)}

    _PRIORITY_ACTIONS = {"top", "up", "down", "bottom"}

    @app.post("/api/set-priority")
    def set_priority(body: SetPriorityBody) -> dict[str, Any]:
        """Reorder selected torrents in qBittorrent's download/seed queue.
        action: top | up | down | bottom."""
        if not body.hashes:
            raise HTTPException(status_code=400, detail="No torrents selected.")
        action = body.action.strip().lower()
        if action not in _PRIORITY_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid action '{body.action}'. Use top, up, down or bottom.",
            )
        try:
            client = state.client()
            # Queue priority is a no-op (and qBittorrent returns 409) unless
            # Torrent Queueing is enabled — fail early with a clear message.
            if not client.queueing_enabled():
                raise HTTPException(
                    status_code=400,
                    detail="Torrent Queueing is disabled in qBittorrent, so download "
                           "priority has no effect. Enable it first (there's a toggle "
                           "in the toolbar, or qBittorrent → Options → BitTorrent).",
                )
            {
                "top": client.top_priority,
                "up": client.increase_priority,
                "down": client.decrease_priority,
                "bottom": client.bottom_priority,
            }[action](body.hashes)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        return {"ok": True, "action": action, "count": len(body.hashes)}

    # qBittorrent per-file priority codes. "Mixed" is a display-only state
    # (files differ) — it can't be set, only produced by editing files
    # individually, so it isn't an accepted value here.
    _FILE_PRIORITIES = {0, 1, 6, 7}

    @app.post("/api/set-file-priority")
    def set_file_priority(body: SetFilePriorityBody) -> dict[str, Any]:
        """Set the download priority of ALL files in each selected torrent, in
        bulk. priority: 0 = do not download, 1 = normal, 6 = high, 7 = maximal."""
        if not body.hashes:
            raise HTTPException(status_code=400, detail="No torrents selected.")
        if body.priority not in _FILE_PRIORITIES:
            raise HTTPException(
                status_code=400,
                detail="priority must be 0 (do not download), 1 (normal), "
                       "6 (high) or 7 (maximal).",
            )
        client = state.client()
        changed = 0        # torrents whose files were updated
        no_files = 0       # torrents with no files yet (e.g. fetching metadata)
        errors: list[dict[str, str]] = []
        for h in body.hashes:
            try:
                n = client.set_all_files_priority(h, body.priority)
                if n:
                    changed += 1
                else:
                    no_files += 1
            except Exception as exc:  # noqa: BLE001 — collect, don't abort the batch
                errors.append({"hash": h, "error": str(exc)})
        return {"ok": True, "priority": body.priority, "count": changed,
                "no_files": no_files, "errors": errors}

    @app.get("/api/queueing")
    def get_queueing() -> dict[str, Any]:
        try:
            return {"enabled": state.client().queueing_enabled()}
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)

    @app.put("/api/queueing")
    def set_queueing(body: QueueingBody) -> dict[str, Any]:
        try:
            state.client().set_queueing_enabled(body.enabled)
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        return {"ok": True, "enabled": body.enabled}

    @app.get("/api/default-save-path")
    def get_default_save_path() -> dict[str, Any]:
        try:
            return {"save_path": state.client().default_save_path()}
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)

    @app.put("/api/default-save-path")
    def set_default_save_path(body: SavePathBody) -> dict[str, Any]:
        path = body.save_path.strip()
        if not path:
            raise HTTPException(status_code=400, detail="Save path required.")
        try:
            state.client().set_default_save_path(path)
        except Exception as exc:  # noqa: BLE001
            raise _err(exc)
        return {"ok": True, "save_path": path}

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

