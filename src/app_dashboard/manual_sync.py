"""Single-process orchestration for operator-triggered data refreshes."""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from threading import Lock, Thread
from typing import Callable

import httpx

from app_dashboard.active_subscriptions import sync_active_subscriptions
from app_dashboard.catalog import AppConfig
from app_dashboard.ga4 import build_client as build_ga4_client
from app_dashboard.ga4 import sync_ga4
from app_dashboard.partner_api import PartnerClient
from app_dashboard.pipeline import run_sync, sync_transactions

logger = logging.getLogger(__name__)

MODES = frozenset({"fresh", "all"})


class SyncAlreadyRunning(RuntimeError):
    pass


class InvalidSyncMode(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ManualSyncCoordinator:
    """Run at most one manual refresh and expose a safe progress snapshot."""

    def __init__(
        self,
        conn_factory,
        settings,
        *,
        source_runner: Callable[[AppConfig, str, bool], None] | None = None,
        thread_factory=Thread,
    ):
        self._conn_factory = conn_factory
        self._settings = settings
        self._source_runner = source_runner
        self._thread_factory = thread_factory
        self._lock = Lock()
        self._status = {
            "state": "idle",
            "mode": None,
            "scope": [],
            "completed_steps": 0,
            "total_steps": 0,
            "current_app": None,
            "current_source": None,
            "started_at": None,
            "finished_at": None,
            "errors": [],
        }

    def status(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._status)

    @staticmethod
    def _sources(app: AppConfig) -> list[str]:
        sources = ["lifecycle", "transactions", "subscriptions"]
        if app.ga4_property_id and app.ga4_credentials_json:
            sources.append("ga4")
        return sources

    def start(self, apps: list[AppConfig], *, mode: str) -> dict:
        if mode not in MODES:
            raise InvalidSyncMode(mode)
        if not apps:
            raise ValueError("No apps selected")
        total_steps = sum(len(self._sources(app)) for app in apps)
        with self._lock:
            if self._status["state"] == "running":
                raise SyncAlreadyRunning()
            self._status = {
                "state": "running",
                "mode": mode,
                "scope": [app.slug for app in apps],
                "completed_steps": 0,
                "total_steps": total_steps,
                "current_app": None,
                "current_source": None,
                "started_at": _now(),
                "finished_at": None,
                "errors": [],
            }
        thread = self._thread_factory(
            target=lambda: self._run(list(apps), mode == "all"), daemon=True
        )
        thread.start()
        return self.status()

    def _run(self, apps: list[AppConfig], full_history: bool) -> None:
        clients = {
            app.partner_org_id: PartnerClient(app.partner_token, app.partner_org_id)
            for app in apps
        }
        for app in apps:
            for source in self._sources(app):
                with self._lock:
                    self._status["current_app"] = app.slug
                    self._status["current_source"] = source
                try:
                    if self._source_runner is not None:
                        self._source_runner(app, source, full_history)
                    else:
                        self._run_source(
                            app, source, full_history, clients[app.partner_org_id]
                        )
                except Exception as exc:
                    logger.exception("manual %s sync failed for %s", source, app.slug)
                    with self._lock:
                        self._status["errors"].append({
                            "app": app.slug,
                            "source": source,
                            "error": type(exc).__name__,
                        })
                finally:
                    with self._lock:
                        self._status["completed_steps"] += 1
        with self._lock:
            self._status["state"] = (
                "failed" if self._status["errors"] else "complete"
            )
            self._status["current_app"] = None
            self._status["current_source"] = None
            self._status["finished_at"] = _now()

    def _run_source(
        self,
        app: AppConfig,
        source: str,
        full_history: bool,
        partner_client,
    ) -> None:
        conn = self._conn_factory()
        try:
            if source == "lifecycle":
                run_sync(
                    conn,
                    partner_client,
                    app,
                    self._settings,
                    http_post=httpx.post,
                    full_history=full_history,
                )
            elif source == "transactions":
                sync_transactions(
                    conn,
                    partner_client,
                    app,
                    self._settings,
                    full_history=full_history,
                )
            elif source == "subscriptions":
                sync_active_subscriptions(conn, partner_client, app)
            elif source == "ga4":
                client = build_ga4_client(app.ga4_credentials_json or "")
                sync_ga4(conn, client, app, force_full=full_history)
            else:
                raise ValueError(f"Unknown sync source {source}")
        finally:
            conn.close()
