from datetime import datetime, timezone
from decimal import Decimal

from app_dashboard.scope import Scope
from app_dashboard.trials import current_trials


NOW = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)


def _shop(db, app_id, gid, name):
    db.execute(
        "insert into shops (app_id, shop_gid, shop_name, shop_domain, install_state) "
        "values (%s, %s, %s, %s, 'installed')",
        (app_id, gid, name, f"{gid}.myshopify.com"),
    )


def _trial(db, app_id, gid, sub_id, ends_at, *, cancel=False):
    db.execute(
        """
        insert into active_subscriptions (
            app_id, shop_gid, legacy_subscription_id, billing_period,
            trial_ends_at, cancel_at_end_of_cycle, item_description, observed_at
        ) values (%s, %s, %s, 'EVERY_30_DAYS', %s, %s, 'Growth', %s)
        """,
        (app_id, gid, sub_id, ends_at, cancel, NOW),
    )


def test_current_trials_are_scoped_and_join_the_exact_subscription(
    db, test_app, app_factory
):
    other = app_factory(slug="other", name="Other")
    _shop(db, test_app.id, "soon", "Soon Shop")
    _shop(db, test_app.id, "later", "Later Shop")
    _shop(db, test_app.id, "expired", "Expired Shop")
    _shop(db, other.id, "other", "Other Shop")
    for app_id, gid, sub_id, amount in (
        (test_app.id, "soon", "sub-soon", "20.00"),
        (test_app.id, "later", "sub-later", "40.00"),
        (test_app.id, "expired", "sub-expired", "10.00"),
        (other.id, "other", "sub-other", "99.00"),
    ):
        db.execute(
            "insert into subscriptions "
            "(app_id, id, shop_gid, monthly_amount, converted_at) "
            "values (%s, %s, %s, %s, '2026-08-01Z')",
            (app_id, sub_id, gid, amount),
        )
    _trial(db, test_app.id, "soon", "sub-soon", "2026-08-11T20:00:00Z", cancel=True)
    _trial(db, test_app.id, "later", "sub-later", "2026-08-25Z")
    _trial(db, test_app.id, "expired", "sub-expired", "2026-08-10Z")
    _trial(db, other.id, "other", "sub-other", "2026-08-20Z")
    db.execute(
        "insert into sync_state (app_id, source, last_synced_at) values "
        "(%s, 'partner_active_subscriptions', %s), "
        "(%s, 'partner_active_subscriptions', %s)",
        (test_app.id, NOW, other.id, NOW),
    )
    db.commit()

    report = current_trials(db, Scope.for_app(test_app.id), now=NOW)

    assert [row["shop"] for row in report["rows"]] == ["Soon Shop", "Later Shop"]
    assert report["rows"][0]["hours_left"] == 8
    assert report["rows"][0]["days_left"] == 1
    assert report["count"] == 2
    assert report["ending_soon"] == 1
    assert report["cancel_scheduled"] == 1
    assert report["potential_mrr"] == Decimal("60.00")
    assert report["converting_mrr"] == Decimal("40.00")
    assert report["cancelling_mrr"] == Decimal("20.00")
    assert report["known_mrr_count"] == 2
    assert report["sync_complete"] is True


def test_unknown_current_subscription_price_is_not_guessed(db, test_app):
    _shop(db, test_app.id, "trial", "Trial Shop")
    db.execute(
        "insert into subscriptions "
        "(app_id, id, shop_gid, monthly_amount, converted_at) "
        "values (%s, 'old-sub', 'trial', 25, '2026-01-01Z')",
        (test_app.id,),
    )
    _trial(db, test_app.id, "trial", "new-sub", "2026-08-20Z")
    db.commit()

    report = current_trials(db, Scope.for_app(test_app.id), now=NOW)

    assert report["rows"][0]["monthly_amount"] is None
    assert report["potential_mrr"] == Decimal("0")
    assert report["known_mrr_count"] == 0
    assert report["sync_complete"] is False
