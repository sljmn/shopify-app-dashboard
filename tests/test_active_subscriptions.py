from datetime import datetime, timezone

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
    assert db.execute("select count(*) from active_subscriptions").fetchone()[0] == 0
