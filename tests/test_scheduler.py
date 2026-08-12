from dataclasses import replace

from app_dashboard.scheduler import (
    run_active_subscriptions_job,
    run_all_apps,
    run_app_discovery_job,
    run_aso_job,
    run_category_discovery_job,
    run_developer_catalog_job,
    run_rank_tracker_job,
    run_review_collection_job,
    run_sync_job,
    run_watchlist_job,
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


def test_rank_tracker_job_contains_keyword_failures_and_closes(monkeypatch):
    class RankConn(FakeConn):
        def execute(self, sql):
            class Rows:
                def fetchall(self):
                    return [(1,), (2,)]
            return Rows()

    conn = RankConn()

    def sync(_conn, keyword_id):
        if keyword_id == 2:
            raise RuntimeError("bad keyword")
        return {"status": "ready", "results": 100}

    monkeypatch.setattr("app_dashboard.rank_collector.sync_keyword_rankings", sync)
    assert run_rank_tracker_job(lambda: conn) == [
        {"keyword_id": 1, "status": "ready", "results": 100},
        {"keyword_id": 2, "status": "failed", "error": "RuntimeError"},
    ]
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


def test_aso_job_skips_unconfigured_apps_and_closes_connections(
    monkeypatch, test_app, app_factory
):
    configured = replace(
        test_app, ga4_property_id="123", ga4_credentials_json="{}"
    )
    unconfigured = app_factory(slug="no-ga4")
    conn = FakeConn()
    capability = type("Capability", (), {
        "statuses": {"aso_keywords": "ready", "aso_attribution": "unsupported"},
        "fields": {"keyword": "searchTerm"},
    })()
    monkeypatch.setattr("app_dashboard.ga4.build_client", lambda value: object())
    monkeypatch.setattr("app_dashboard.aso_ga4.sync_capabilities", lambda *a: capability)
    monkeypatch.setattr("app_dashboard.aso_ga4.sync_aso_keywords", lambda *a, **k: 4)

    result = run_aso_job(lambda: conn, [configured, unconfigured], object())

    assert result[0]["app"] == configured.slug
    assert result[0]["written"] == {"keywords": 4, "attribution": 0}
    assert conn.closed_count == 1


def test_aso_job_skips_attribution_without_shop_domain(monkeypatch, test_app):
    configured = replace(
        test_app, ga4_property_id="123", ga4_credentials_json="{}"
    )
    conn = FakeConn()
    capability = type("Capability", (), {
        "statuses": {"aso_keywords": "ready", "aso_attribution": "partial"},
        "fields": {"page_location": "pageLocation", "source": "sessionSource"},
    })()
    monkeypatch.setattr("app_dashboard.ga4.build_client", lambda value: object())
    monkeypatch.setattr("app_dashboard.aso_ga4.sync_capabilities", lambda *a: capability)
    monkeypatch.setattr("app_dashboard.aso_ga4.sync_aso_keywords", lambda *a, **k: 4)

    def unexpected_attribution(*args, **kwargs):
        raise AssertionError("attribution requires a shop_domain dimension")

    monkeypatch.setattr(
        "app_dashboard.aso_ga4.sync_install_sources", unexpected_attribution
    )

    result = run_aso_job(lambda: conn, [configured], object())

    assert result[0]["ok"] is True
    assert result[0]["written"] == {"keywords": 4, "attribution": 0}
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

    digest = [kw for trigger, kw in fake.jobs if kw.get("id") == "weekly_digest"]
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
    discovery = {kw["id"]: kw for trigger, kw in fake.jobs
                 if kw.get("id", "").startswith("app_store_")}
    assert discovery["app_store_discovery"]["hour"] == 3
    assert discovery["app_store_categories"]["day_of_week"] == "tue,fri"
    watchlist = [kw for trigger, kw in fake.jobs if kw.get("id") == "watchlist_listings"]
    assert len(watchlist) == 1 and watchlist[0]["hours"] == 24
    reviews = [kw for trigger, kw in fake.jobs if kw.get("id") == "watchlist_reviews"]
    assert len(reviews) == 1 and reviews[0]["hours"] == 1
    listings = [kw for trigger, kw in fake.jobs if kw.get("id") == "aso_listings"]
    assert len(listings) == 1 and listings[0]["hours"] == 24
    rank_tracker = [kw for trigger, kw in fake.jobs if kw.get("id") == "aso_rank_tracker"]
    assert len(rank_tracker) == 1
    assert rank_tracker[0]["hour"] == 6 and rank_tracker[0]["minute"] == 15
    assert started["yes"] is True


def test_discovery_jobs_close_connections_and_contain_failures(monkeypatch):
    app_conn = FakeConn()
    category_conn = FakeConn()
    monkeypatch.setattr(
        "app_dashboard.app_store_discovery.run_app_discovery",
        lambda conn: {"seen": 12, "new": 2, "baseline": False},
    )

    def fail_categories(conn):
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(
        "app_dashboard.app_store_discovery.run_category_discovery", fail_categories
    )

    assert run_app_discovery_job(lambda: app_conn) == {
        "ok": True, "seen": 12, "new": 2, "baseline": False,
    }
    assert run_category_discovery_job(lambda: category_conn) == {
        "ok": False, "error": "RuntimeError",
    }
    assert app_conn.closed_count == 1
    assert category_conn.closed_count == 1


def test_successful_category_discovery_adds_automatic_follows(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(
        "app_dashboard.app_store_discovery.run_category_discovery",
        lambda current: {"categories": 2, "memberships": 4},
    )
    monkeypatch.setattr(
        "app_dashboard.discovery_watchlist.follow_automatic_candidates",
        lambda current: {"followed": 2, "already_followed": 1},
    )
    monkeypatch.setattr(
        "app_dashboard.discovery_watchlist.queue_category_alerts",
        lambda current: 3,
    )
    assert run_category_discovery_job(lambda: conn) == {
        "ok": True, "categories": 2, "memberships": 4,
        "watchlist": {"followed": 2, "already_followed": 1},
        "alerts_queued": 3,
    }
    assert conn.closed_count == 1


def test_watchlist_job_isolates_apps_and_closes_connections(monkeypatch, tmp_path):
    from types import SimpleNamespace

    index = FakeConn()
    alpha = FakeConn()
    beta = FakeConn()
    connections = iter([index, alpha, beta])
    monkeypatch.setattr(
        "app_dashboard.discovery_watchlist.active_watched_apps",
        lambda conn: [(1, "alpha"), (2, "beta")],
    )

    def sync(conn, app_id, handle, **kwargs):
        if handle == "beta":
            raise RuntimeError("failed")
        return {"handle": handle, "ok": True, "created": True, "changes": 0}

    monkeypatch.setattr(
        "app_dashboard.watchlist_collector.sync_followed_listing", sync
    )
    results = run_watchlist_job(
        lambda: next(connections),
        SimpleNamespace(watchlist_media_path=tmp_path, watchlist_concurrency=1),
    )
    assert results == [
        {"handle": "alpha", "ok": True, "created": True, "changes": 0},
        {"handle": "beta", "ok": False, "error": "RuntimeError"},
    ]
    assert [conn.closed_count for conn in (index, alpha, beta)] == [1, 1, 1]


def test_review_job_isolates_apps_and_closes_connections(monkeypatch):
    from types import SimpleNamespace

    index = FakeConn()
    alpha = FakeConn()
    beta = FakeConn()
    connections = iter([index, alpha, beta])
    monkeypatch.setattr(
        "app_dashboard.review_collector.review_sync_targets",
        lambda conn, **kwargs: [(1, "alpha"), (2, "beta")],
    )

    def sync(conn, app_id, handle, **kwargs):
        if handle == "beta":
            raise RuntimeError("failed")
        return {"handle": handle, "ok": True, "captured": 3}

    monkeypatch.setattr("app_dashboard.review_collector.sync_app_reviews", sync)
    results = run_review_collection_job(
        lambda: next(connections), SimpleNamespace(watchlist_concurrency=1),
    )
    assert results == [
        {"handle": "alpha", "ok": True, "captured": 3},
        {"handle": "beta", "ok": False, "error": "RuntimeError"},
    ]
    assert [conn.closed_count for conn in (index, alpha, beta)] == [1, 1, 1]


def test_developer_catalog_job_isolates_developers_and_closes_connections(monkeypatch):
    from types import SimpleNamespace

    index = FakeConn()
    alpha = FakeConn()
    beta = FakeConn()
    connections = iter([index, alpha, beta])
    monkeypatch.setattr(
        "app_dashboard.developer_catalog.developers_due_for_refresh",
        lambda conn: [10, 20],
    )

    def sync(conn, developer_id):
        if developer_id == 20:
            raise RuntimeError("page failed")
        return {"developer_id": developer_id, "status": "ready"}

    monkeypatch.setattr("app_dashboard.developer_catalog.sync_developer_catalog", sync)
    results = run_developer_catalog_job(
        lambda: next(connections), SimpleNamespace(watchlist_concurrency=1),
    )
    assert results == [
        {"developer_id": 10, "status": "ready"},
        {"developer_id": 20, "status": "failed", "error": "RuntimeError"},
    ]
    assert [conn.closed_count for conn in (index, alpha, beta)] == [1, 1, 1]
