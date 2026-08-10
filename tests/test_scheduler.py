from dataclasses import replace

from app_dashboard.scheduler import (
    run_active_subscriptions_job,
    run_all_apps,
    run_sync_job,
)


class FakeConn:
    def __init__(self):
        self.closed_count = 0

    def close(self):
        self.closed_count += 1


def test_run_sync_job_closes_connection_on_success(monkeypatch, test_app):
    conn = FakeConn()
    monkeypatch.setattr("app_dashboard.scheduler.run_sync", lambda *a, **k: {"raw_inserted": 0})
    run_sync_job(lambda: conn, apps=[test_app], settings=object())
    assert conn.closed_count == 1


def test_run_sync_job_closes_connection_even_if_sync_raises(monkeypatch, test_app):
    conn = FakeConn()

    def boom(*a, **k):
        raise RuntimeError("sync failed")

    monkeypatch.setattr("app_dashboard.scheduler.run_sync", boom)
    result = run_sync_job(lambda: conn, apps=[test_app], settings=object())
    assert conn.closed_count == 1
    assert result == [{"app": test_app.slug, "ok": False, "error": "sync failed"}]


def test_active_subscription_job_closes_each_app_connection(monkeypatch, test_app):
    conn = FakeConn()
    monkeypatch.setattr(
        "app_dashboard.scheduler.sync_active_subscriptions",
        lambda *args, **kwargs: {"queried": 0},
    )

    run_active_subscriptions_job(lambda: conn, [test_app], object())

    assert conn.closed_count == 1


def test_all_apps_continue_after_failure_and_share_org_clients(
    monkeypatch, app_factory
):
    alpha = app_factory(slug="alpha")
    beta_stored = app_factory(slug="beta")
    gamma = app_factory(slug="gamma")
    beta = replace(
        beta_stored,
        partner_org_id=alpha.partner_org_id,
        partner_token=alpha.partner_token,
    )
    created = []

    def client(token, org_id):
        value = object()
        created.append((token, org_id, value))
        return value

    monkeypatch.setattr("app_dashboard.scheduler.PartnerClient", client)
    seen = []

    def sync_one(conn_factory, client, app, settings):
        seen.append(app.slug)
        if app.slug == "beta":
            raise RuntimeError("beta failed")
        return {"app": app.slug, "ok": True}

    results = run_all_apps(lambda: None, [alpha, beta, gamma], object(), sync_one)
    assert seen == ["alpha", "beta", "gamma"]
    assert results == [
        {"app": "alpha", "ok": True},
        {"app": "beta", "ok": False, "error": "beta failed"},
        {"app": "gamma", "ok": True},
    ]
    assert len(created) == 2


def test_weekly_digest_is_registered_at_the_configured_local_time(monkeypatch, test_app):
    """Only the wiring: send_weekly_digest itself is tested in test_digest."""
    from types import SimpleNamespace

    import app_dashboard.scheduler as sched

    monkeypatch.setattr(sched, "PartnerClient", lambda *a, **k: object())

    started = {}

    class FakeScheduler:
        def __init__(self):
            self.jobs = []

        def add_job(self, func, trigger, **kw):
            self.jobs.append((trigger, kw))

        def start(self):
            started["yes"] = True

    fake = FakeScheduler()
    monkeypatch.setattr(sched, "BackgroundScheduler", lambda: fake)
    sched.start_scheduler(lambda: None, SimpleNamespace(
        partner_api_token="t", partner_org_id="1", poll_interval_minutes=15,
        digest_day_of_week="tue", digest_hour=7, digest_timezone="Europe/Berlin"),
        [test_app])

    digest = [kw for trigger, kw in fake.jobs if trigger == "cron"]
    assert len(digest) == 1
    # Read off settings rather than hardcoded, so a deployment that wants its
    # digest on Tuesday morning in Berlin gets it there.
    assert digest[0]["day_of_week"] == "tue"
    assert digest[0]["hour"] == 7 and digest[0]["minute"] == 0
    assert digest[0]["timezone"] == "Europe/Berlin"
    active_subscriptions = [
        kw for trigger, kw in fake.jobs
        if trigger == "interval" and kw.get("id") == "active_subscriptions"
    ]
    assert len(active_subscriptions) == 1
    assert active_subscriptions[0]["hours"] == 6
    assert started["yes"] is True
