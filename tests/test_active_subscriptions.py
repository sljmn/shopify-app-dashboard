from datetime import datetime, timezone
from decimal import Decimal

from app_dashboard.active_subscriptions import (
    SOURCE,
    sync_active_subscriptions,
)


class FakeClient: ...


def test_refresh_only_queries_installed_shops_and_replaces_current_state(
    db, test_app, monkeypatch
):
    db.execute(
        "insert into shops (app_id, shop_gid, install_state) values "
        "(%s, 'active-shop', 'installed'), "
        "(%s, 'gone-shop', 'uninstalled')",
        (test_app.id, test_app.id),
    )
    db.execute(
        "insert into active_subscriptions "
        "(app_id, shop_gid, legacy_subscription_id, observed_at) "
        "values (%s, 'active-shop', 'stale-sub', '2026-08-01Z'), "
        "(%s, 'gone-shop', 'keep-unqueried', '2026-08-01Z')",
        (test_app.id, test_app.id),
    )
    seen = []

    def fetch(client, *, app_id, shop_id):
        seen.append((app_id, shop_id))
        return {
            "legacy_subscription_id": "current-sub",
            "billing_period": "EVERY_30_DAYS",
            "trial_ends_at": "2026-08-20T12:00:00Z",
            "cancel_at_end_of_cycle": False,
            "item_handle": "starter",
            "item_description": "Starter",
            "currency_code": "USD",
            "payload": {"source": "test"},
        }

    monkeypatch.setattr(
        "app_dashboard.active_subscriptions.fetch_active_subscription", fetch
    )
    summary = sync_active_subscriptions(
        db, FakeClient(), test_app, sleep=lambda _: None,
        now=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
    )

    assert seen == [(test_app.partner_app_id, "active-shop")]
    assert summary == {
        "app": test_app.slug,
        "ok": True,
        "queried": 1,
        "stored": 1,
        "removed": 0,
        "trials": 1,
        "reconciled": 0,
    }
    row = db.execute(
        "select legacy_subscription_id, item_handle, observed_at "
        "from active_subscriptions where app_id=%s and shop_gid='active-shop'",
        (test_app.id,),
    ).fetchone()
    assert row[0:2] == ("current-sub", "starter")
    assert row[2] == datetime(2026, 8, 11, tzinfo=timezone.utc)
    assert db.execute(
        "select legacy_subscription_id from active_subscriptions "
        "where app_id=%s and shop_gid='gone-shop'",
        (test_app.id,),
    ).fetchone()[0] == "keep-unqueried"
    assert db.execute(
        "select last_synced_at from sync_state where app_id=%s and source=%s",
        (test_app.id, SOURCE),
    ).fetchone()[0] == datetime(2026, 8, 11, tzinfo=timezone.utc)


def test_nil_response_removes_a_stale_snapshot(db, test_app, monkeypatch):
    db.execute(
        "insert into shops (app_id, shop_gid, install_state) "
        "values (%s, 'active-shop', 'installed')",
        (test_app.id,),
    )
    db.execute(
        "insert into active_subscriptions "
        "(app_id, shop_gid, legacy_subscription_id, observed_at) "
        "values (%s, 'active-shop', 'stale-sub', '2026-08-01Z')",
        (test_app.id,),
    )
    monkeypatch.setattr(
        "app_dashboard.active_subscriptions.fetch_active_subscription",
        lambda *args, **kwargs: None,
    )

    summary = sync_active_subscriptions(
        db, FakeClient(), test_app, sleep=lambda _: None
    )

    assert summary["removed"] == 1
    assert db.execute(
        "select legacy_subscription_id, trial_ends_at "
        "from active_subscriptions where app_id=%s and shop_gid='active-shop'",
        (test_app.id,),
    ).fetchone() == (None, None)


def test_incremental_refresh_only_queries_shops_changed_since_snapshot(
    db, test_app, monkeypatch
):
    db.execute(
        "insert into shops (app_id, shop_gid, install_state) values "
        "(%s, 'unchanged', 'installed'), "
        "(%s, 'event-changed', 'installed'), "
        "(%s, 'payment-changed', 'installed'), "
        "(%s, 'never-checked', 'installed')",
        (test_app.id, test_app.id, test_app.id, test_app.id),
    )
    db.execute(
        "insert into active_subscriptions "
        "(app_id, shop_gid, observed_at) values "
        "(%s, 'unchanged', '2026-08-11T10:00:00Z'), "
        "(%s, 'event-changed', '2026-08-11T10:00:00Z'), "
        "(%s, 'payment-changed', '2026-08-11T10:00:00Z')",
        (test_app.id, test_app.id, test_app.id),
    )
    db.execute(
        "insert into app_events "
        "(app_id, platform_event_id, type, occurred_at, shop_gid) "
        "values (%s, 'new-event', 'subscribed', '2026-08-11T10:01:00Z', "
        "'event-changed')",
        (test_app.id,),
    )
    db.execute(
        "insert into transactions "
        "(app_id, id, type, created_at, shop_gid) "
        "values (%s, 'new-payment', 'AppSubscriptionSale', "
        "'2026-08-11T10:02:00Z', 'payment-changed')",
        (test_app.id,),
    )
    seen = []

    def fetch(client, *, app_id, shop_id):
        seen.append(shop_id)
        return None

    monkeypatch.setattr(
        "app_dashboard.active_subscriptions.fetch_active_subscription", fetch
    )

    summary = sync_active_subscriptions(
        db,
        FakeClient(),
        test_app,
        full_refresh=False,
        sleep=lambda _: None,
        now=lambda: datetime(2026, 8, 11, 11, tzinfo=timezone.utc),
    )

    assert seen == ["event-changed", "never-checked", "payment-changed"]
    assert summary["queried"] == 3


def test_empty_snapshot_prevents_requerying_a_free_shop(db, test_app, monkeypatch):
    db.execute(
        "insert into shops (app_id, shop_gid, install_state) "
        "values (%s, 'free-shop', 'installed')",
        (test_app.id,),
    )
    seen = []
    monkeypatch.setattr(
        "app_dashboard.active_subscriptions.fetch_active_subscription",
        lambda *args, **kwargs: seen.append(kwargs["shop_id"]),
    )

    sync_active_subscriptions(
        db, FakeClient(), test_app, full_refresh=False, sleep=lambda _: None
    )
    sync_active_subscriptions(
        db, FakeClient(), test_app, full_refresh=False, sleep=lambda _: None
    )

    assert seen == ["free-shop"]


def test_snapshot_id_mismatch_reconciles_current_mrr_from_latest_payment(
    db, test_app, monkeypatch
):
    changed_at = datetime(2026, 5, 24, 15, 39, 38, tzinfo=timezone.utc)
    db.execute(
        "insert into shops (app_id, shop_gid, install_state) "
        "values (%s, 'shop-1', 'installed')",
        (test_app.id,),
    )
    db.execute(
        """insert into subscriptions
               (app_id, id, shop_gid, monthly_amount, billing_type,
                converted_at, churned_at)
           values (%s, 'old-annual', 'shop-1', 8.33, 'ANNUAL',
                   '2026-03-26Z', %s),
                  (%s, 'new-monthly', 'shop-1', 29, 'EVERY_30_DAYS',
                   %s, null)""",
        (test_app.id, changed_at, test_app.id, changed_at),
    )
    db.execute(
        """insert into app_events
               (app_id, platform_event_id, type, occurred_at, net_change, shop_gid)
           values (%s, 'plan-change', 'upgraded', %s, 20.67, 'shop-1')""",
        (test_app.id, changed_at),
    )
    db.execute(
        """insert into transactions
               (app_id, id, type, created_at, shop_gid, charge_gid,
                billing_interval, gross_amount)
           values (%s, 'payment-1', 'AppSubscriptionSale', '2026-04-02Z',
                   'shop-1', 'old-annual', 'ANNUAL', 99.90)""",
        (test_app.id,),
    )

    monkeypatch.setattr(
        "app_dashboard.active_subscriptions.fetch_active_subscription",
        lambda *args, **kwargs: {
            "legacy_subscription_id": "old-annual",
            "billing_period": "ANNUAL",
            "trial_ends_at": None,
            "cancel_at_end_of_cycle": False,
            "item_handle": "starter",
            "item_description": "Starter",
            "currency_code": "USD",
            "payload": {"source": "test"},
        },
    )

    summary = sync_active_subscriptions(
        db, FakeClient(), test_app, sleep=lambda _: None,
        now=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
    )

    assert summary["reconciled"] == 1
    current = db.execute(
        "select id, monthly_amount, billing_type from subscriptions "
        "where app_id=%s and shop_gid='shop-1' and churned_at is null",
        (test_app.id,),
    ).fetchone()
    assert current == ("new-monthly", Decimal("8.33"), "ANNUAL")
    movement = db.execute(
        "select type, net_change, plan_amount, plan_interval from app_events "
        "where app_id=%s and platform_event_id='plan-change'",
        (test_app.id,),
    ).fetchone()
    assert movement == (
        "subscription_reconciled", Decimal("0.00"), Decimal("99.90"), "ANNUAL"
    )


def test_snapshot_interval_repairs_annual_mrr_without_a_transaction(
    db, test_app, monkeypatch
):
    converted_at = datetime(2026, 8, 7, tzinfo=timezone.utc)
    db.execute(
        "insert into shops (app_id, shop_gid, install_state) "
        "values (%s, 'shop-1', 'installed')",
        (test_app.id,),
    )
    db.execute(
        """insert into charges
               (app_id, gid, amount, plan_amount, plan_interval, subscription_id)
           values (%s, 'annual-sub', 60, 60, 'EVERY_30_DAYS', 'annual-sub')""",
        (test_app.id,),
    )
    db.execute(
        """insert into subscriptions
               (app_id, id, shop_gid, monthly_amount, billing_type, converted_at)
           values (%s, 'annual-sub', 'shop-1', 60, 'EVERY_30_DAYS', %s)""",
        (test_app.id, converted_at),
    )
    db.execute(
        """insert into app_events
               (app_id, platform_event_id, type, occurred_at, net_change,
                plan_amount, plan_interval, shop_gid)
           values (%s, 'subscribed', 'subscribed', %s, 60, 60,
                   'EVERY_30_DAYS', 'shop-1')""",
        (test_app.id, converted_at),
    )
    monkeypatch.setattr(
        "app_dashboard.active_subscriptions.fetch_active_subscription",
        lambda *args, **kwargs: {
            "legacy_subscription_id": "annual-sub",
            "billing_period": "ANNUAL",
            "trial_ends_at": None,
            "cancel_at_end_of_cycle": False,
            "item_handle": "standard",
            "item_description": "Standard",
            "currency_code": "USD",
            "payload": {"source": "test"},
        },
    )

    summary = sync_active_subscriptions(
        db, FakeClient(), test_app, sleep=lambda _: None,
        now=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
    )

    assert summary["reconciled"] == 1
    assert db.execute(
        "select monthly_amount, billing_type from subscriptions "
        "where app_id=%s and id='annual-sub'",
        (test_app.id,),
    ).fetchone() == (Decimal("5.00"), "ANNUAL")
    assert db.execute(
        "select net_change, plan_amount, plan_interval from app_events "
        "where app_id=%s and platform_event_id='subscribed'",
        (test_app.id,),
    ).fetchone() == (Decimal("5.00"), Decimal("60.00"), "ANNUAL")
