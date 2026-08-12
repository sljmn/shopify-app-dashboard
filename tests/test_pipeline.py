from decimal import Decimal

from app_dashboard.pipeline import (
    PAYOUT_EARNINGS_SOURCE,
    TRANSACTIONS_SOURCE,
    run_sync,
    sync_payout_earnings,
    sync_transactions,
)


class FakeClient: ...


def _settings(**kw):
    from app_dashboard.config import Settings
    return Settings(
        database_url="x", dashboard_username="tester@example.com",
        dashboard_password="suite-only-credential", **kw
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
    s = Settings(database_url="x", dashboard_username="tester@example.com",
                 dashboard_password="suite-only-credential",
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


def test_event_poll_starts_at_newest_page_with_time_overlap(
    db, test_app, monkeypatch
):
    db.execute(
        "insert into sync_state(app_id, source, cursor, last_synced_at) "
        "values (%s, 'partner_api', 'stale-cursor', "
        "        timestamptz '2026-08-11 08:00:00+00')",
        (test_app.id,),
    )
    db.commit()
    calls = []

    def fetch(client, app_id, after_cursor, occurred_at_min):
        calls.append((app_id, after_cursor, occurred_at_min))
        return [], None

    monkeypatch.setattr("app_dashboard.pipeline.fetch_app_events", fetch)
    run_sync(
        db, FakeClient(), test_app, _settings(poll_overlap_minutes=60),
        http_post=lambda *a, **k: None,
    )

    assert calls == [(
        test_app.partner_app_id,
        None,
        "2026-08-11T07:00:00+00:00",
    )]
    assert db.execute(
        "select cursor from sync_state where app_id=%s and source='partner_api'",
        (test_app.id,),
    ).fetchone()[0] is None


def test_full_lifecycle_sync_ignores_saved_cursor(db, test_app, monkeypatch):
    db.execute(
        "insert into sync_state(app_id, source, cursor, last_synced_at) "
        "values (%s, 'partner_api', 'saved-cursor', now())",
        (test_app.id,),
    )
    db.commit()
    seen = []

    def fetch(client, app_id, after_cursor, occurred_at_min):
        seen.append((after_cursor, occurred_at_min))
        return [], None

    monkeypatch.setattr("app_dashboard.pipeline.fetch_app_events", fetch)
    run_sync(
        db, FakeClient(), test_app, _settings(),
        http_post=lambda *a, **k: None, full_history=True,
    )

    assert seen == [(None, None)]


def test_full_transaction_sync_ignores_latest_transaction(db, test_app, monkeypatch):
    monkeypatch.setattr(
        "app_dashboard.pipeline.fetch_transactions",
        lambda *a, **k: ([_txn("existing", "2026-08-02T12:00:00Z")], None),
    )
    sync_transactions(db, FakeClient(), test_app, _settings(), sleep=lambda _: None)
    seen = {}

    def capture(client, **kwargs):
        seen.update(kwargs)
        return [], None

    monkeypatch.setattr("app_dashboard.pipeline.fetch_transactions", capture)
    sync_transactions(
        db, FakeClient(), test_app, _settings(), sleep=lambda _: None,
        full_history=True,
    )

    assert seen["created_at_min"] is None


def test_payout_sync_pages_and_stores_settlement(db, test_app, monkeypatch):
    earning = {
        "id": "earning-1", "event_type": "EARNING_CHARGE_RECURRING",
        "earning_type": "APP_SUBSCRIPTION", "occurred_at": "2026-08-07T10:00:00Z",
        "settlement_date": "2026-08-12", "shop_gid": "shop-1",
        "description": "Subscription", "gross_amount": "19.00",
        "shopify_fee": "0.00", "net_amount": "18.45", "currency_code": "USD",
    }
    pages = [([earning], "next"), ([], None)]
    monkeypatch.setattr(
        "app_dashboard.pipeline.fetch_earnings", lambda *a, **k: pages.pop(0)
    )

    summary = sync_payout_earnings(
        db, FakeClient(), test_app, _settings(), sleep=lambda _: None
    )

    assert summary["earnings_inserted"] == 1
    assert summary["pages"] == 2
    assert db.execute(
        "select settlement_date, net_amount from payout_earnings where app_id=%s",
        (test_app.id,),
    ).fetchone()[1].as_tuple() == Decimal("18.45").as_tuple()
    assert db.execute(
        "select last_synced_at from sync_state where app_id=%s and source=%s",
        (test_app.id, PAYOUT_EARNINGS_SOURCE),
    ).fetchone()[0] is not None


def test_full_payout_sync_starts_at_first_transaction(db, test_app, monkeypatch):
    db.execute(
        """insert into transactions
               (app_id,id,type,created_at,net_amount,currency_code)
           values (%s,'first','AppSubscriptionSale','2024-01-02Z',18.45,'USD')""",
        (test_app.id,),
    )
    seen = []

    def fetch(client, **kwargs):
        seen.append(kwargs)
        return [], None

    monkeypatch.setattr("app_dashboard.pipeline.fetch_earnings", fetch)
    sync_payout_earnings(
        db, FakeClient(), test_app, _settings(), sleep=lambda _: None,
        full_history=True,
    )

    assert len(seen) >= 3
    assert seen[0]["occurred_at_min"].startswith("2024-01-02")
    assert all(seen[index]["occurred_at_max"] < seen[index + 1]["occurred_at_min"]
               for index in range(len(seen) - 1))
