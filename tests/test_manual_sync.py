from dataclasses import replace

import pytest

from app_dashboard.manual_sync import ManualSyncCoordinator, SyncAlreadyRunning


class InlineThread:
    def __init__(self, target, daemon=True):
        self.target = target

    def start(self):
        self.target()


class HoldingThread:
    def __init__(self, target, daemon=True):
        self.target = target

    def start(self):
        pass


def test_coordinator_runs_every_source_for_scoped_apps(app_factory):
    alpha = app_factory(slug="alpha")
    beta = app_factory(slug="beta")
    calls = []
    coordinator = ManualSyncCoordinator(
        lambda: None,
        object(),
        source_runner=lambda app, source, full: calls.append(
            (app.slug, source, full)
        ),
        thread_factory=InlineThread,
    )

    coordinator.start([alpha, beta], mode="all")

    assert calls == [
        ("alpha", "lifecycle", True),
        ("alpha", "transactions", True),
        ("alpha", "subscriptions", True),
        ("beta", "lifecycle", True),
        ("beta", "transactions", True),
        ("beta", "subscriptions", True),
    ]
    status = coordinator.status()
    assert status["state"] == "complete"
    assert status["scope"] == ["alpha", "beta"]
    assert status["completed_steps"] == status["total_steps"] == 6


def test_coordinator_includes_ga4_only_when_configured(app_factory):
    app = app_factory(slug="traffic")
    app = replace(app, ga4_property_id="123", ga4_credentials_json="{}")
    calls = []
    coordinator = ManualSyncCoordinator(
        lambda: None,
        object(),
        source_runner=lambda candidate, source, full: calls.append(source),
        thread_factory=InlineThread,
    )

    coordinator.start([app], mode="fresh")

    assert calls == ["lifecycle", "transactions", "subscriptions", "ga4"]


def test_coordinator_records_failure_and_continues(app_factory):
    app = app_factory(slug="alpha")
    calls = []

    def run(candidate, source, full):
        calls.append(source)
        if source == "transactions":
            raise RuntimeError("secret response body")

    coordinator = ManualSyncCoordinator(
        lambda: None, object(), source_runner=run, thread_factory=InlineThread
    )
    coordinator.start([app], mode="fresh")

    assert calls == ["lifecycle", "transactions", "subscriptions"]
    status = coordinator.status()
    assert status["state"] == "failed"
    assert status["errors"] == [
        {"app": "alpha", "source": "transactions", "error": "RuntimeError"}
    ]
    assert "secret response body" not in str(status)


def test_coordinator_rejects_a_second_running_job(app_factory):
    app = app_factory(slug="alpha")
    coordinator = ManualSyncCoordinator(
        lambda: None,
        object(),
        source_runner=lambda *args: None,
        thread_factory=HoldingThread,
    )
    coordinator.start([app], mode="fresh")

    with pytest.raises(SyncAlreadyRunning):
        coordinator.start([app], mode="all")
