from app_dashboard.pipeline import TRANSACTIONS_SOURCE, run_sync, sync_transactions


class FakeClient: ...


def _settings(**kw):
    from app_dashboard.config import Settings
    return Settings(
        database_url="x", dashboard_users="tester:suite-only-credential", **kw
    )


def _txn(id, created_at, gross="19.0", net="18.45", type="AppSubscriptionSale"):
    return dict(id=id, type=type, created_at=created_at, shop_gid="gid://s/1",
                charge_gid="gid://c/1", billing_interval="EVERY_30_DAYS",
                gross_amount=gross, shopify_fee="0.0", net_amount=net,
                currency_code="USD")


def test_run_sync_ingests_derives_and_notifies(db, test_app, monkeypatch):
    # one install + subscribe page, then empty
    pages = [([
        dict(id="r1", type="RELATIONSHIP_INSTALLED", occurred_at="2026-06-01T00:00:00Z",
             shop_gid="ai1", charge_gid=None, payload={}),
    ], None)]
    monkeypatch.setattr("app_dashboard.pipeline.fetch_app_events",
                        lambda *a, **k: pages.pop(0))
    db.execute("insert into shops(app_id,shop_gid,email,install_state) "
               "values (%s,'ai1','j@x.com','')", (test_app.id,)); db.commit()
    sent = []
    from app_dashboard.config import Settings
    s = Settings(database_url="x", dashboard_users="tester:suite-only-credential",
                 slack_webhook_url="http://hook")
    summary = run_sync(db, FakeClient(), test_app, s,
                       http_post=lambda url, json: sent.append(json) or type("R",(),{"status_code":200})())
    assert summary["raw_inserted"] == 1
    assert summary["app"] == test_app.slug
    assert summary["alerts_sent"] == 1
    assert len(sent) == 1


def test_sync_transactions_pages_and_stores(db, test_app, monkeypatch):
    pages = [
        ([_txn("t1", "2026-08-01T00:00:00Z")], "cur1"),
        ([_txn("t2", "2026-08-02T00:00:00Z")], None),
    ]
    monkeypatch.setattr("app_dashboard.pipeline.fetch_transactions", lambda *a, **k: pages.pop(0))
    summary = sync_transactions(
        db, FakeClient(), test_app, _settings(), sleep=lambda _: None
    )

    assert summary["transactions_inserted"] == 2
    assert summary["pages"] == 2
    # First run has no bound: pull the whole history rather than a window.
    assert summary["since"] is None
    (n,) = db.execute("select count(*) from transactions").fetchone()
    assert n == 2
    # Its own sync_state row, so the events cursor is untouched.
    (last,) = db.execute(
        "select last_synced_at from sync_state where app_id=%s and source = %s",
        (test_app.id, TRANSACTIONS_SOURCE),
    ).fetchone()
    assert last is not None


def test_sync_transactions_rewinds_by_overlap_and_dedupes(db, test_app, monkeypatch):
    monkeypatch.setattr("app_dashboard.pipeline.fetch_transactions",
                        lambda *a, **k: ([_txn("t1", "2026-08-02T12:00:00Z")], None))
    sync_transactions(db, FakeClient(), test_app, _settings(), sleep=lambda _: None)

    seen = {}

    def capture(client, **kwargs):
        seen.update(kwargs)
        # Same row again (the overlap replay) plus a settled net amount.
        return [_txn("t1", "2026-08-02T12:00:00Z", net="18.50")], None

    monkeypatch.setattr("app_dashboard.pipeline.fetch_transactions", capture)
    summary = sync_transactions(db, FakeClient(), test_app,
                                _settings(poll_overlap_minutes=60),
                                sleep=lambda _: None)

    # The window is derived from the newest stored row, not from a cursor.
    assert seen["created_at_min"].startswith("2026-08-02T11:00:00+00:00")
    # A replayed row is not a new payment...
    assert summary["transactions_inserted"] == 0
    # ...but its amounts do refresh, because Shopify settles after creating.
    (net,) = db.execute(
        "select net_amount from transactions where app_id=%s and id = 't1'",
        (test_app.id,),
    ).fetchone()
    assert str(net) == "18.50"


def test_event_cursors_are_independent_per_app(db, app_factory, monkeypatch):
    alpha = app_factory(slug="alpha")
    beta = app_factory(slug="beta")
    calls = []

    def fetch(client, app_id, after_cursor):
        calls.append((app_id, after_cursor))
        return [], f"next-{app_id}" if after_cursor is None else None

    monkeypatch.setattr("app_dashboard.pipeline.fetch_app_events", fetch)
    settings = _settings()
    run_sync(db, FakeClient(), alpha, settings, http_post=lambda *a, **k: None)
    run_sync(db, FakeClient(), beta, settings, http_post=lambda *a, **k: None)

    cursors = dict(db.execute(
        "select app_id, cursor from sync_state where source='partner_api'"
    ).fetchall())
    assert cursors == {
        alpha.id: f"next-{alpha.partner_app_id}",
        beta.id: f"next-{beta.partner_app_id}",
    }
    assert (alpha.partner_app_id, None) in calls
    assert (beta.partner_app_id, None) in calls
