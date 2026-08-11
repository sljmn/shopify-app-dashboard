from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app_dashboard.period_report import build_period_report, sort_rows
from app_dashboard.scope import Scope


def _shop(db, app_id, gid):
    db.execute(
        """insert into shops (app_id, shop_gid, install_state)
           values (%s, %s, 'installed')""",
        (app_id, gid),
    )


def _event(db, app_id, event_id, kind, occurred_at, shop_gid="shop"):
    db.execute(
        """insert into app_events
               (app_id, platform_event_id, type, occurred_at, shop_gid)
           values (%s, %s, %s, %s, %s)""",
        (app_id, event_id, kind, occurred_at, shop_gid),
    )


def _subscription(
    db, app_id, sub_id, shop_gid, amount, converted_at, churned_at=None
):
    db.execute(
        """insert into subscriptions
               (app_id, id, shop_gid, monthly_amount, converted_at, churned_at)
           values (%s, %s, %s, %s, %s, %s)""",
        (app_id, sub_id, shop_gid, amount, converted_at, churned_at),
    )


def _transaction(
    db,
    app_id,
    txn_id,
    net,
    created_at,
    kind="AppSubscriptionSale",
    shop_gid="shop",
):
    db.execute(
        """insert into transactions
               (app_id, id, type, created_at, shop_gid, gross_amount,
                shopify_fee, net_amount, currency_code)
           values (%s, %s, %s, %s, %s, %s, 0, %s, 'USD')""",
        (app_id, txn_id, kind, created_at, shop_gid, net, net),
    )


def test_period_report_combines_events_mrr_and_cash_by_app(
    db, app_factory, test_app
):
    beta = app_factory(slug="beta", name="Beta")
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 11, tzinfo=timezone.utc)
    previous_start = start - (end - start)
    _shop(db, test_app.id, "alpha")
    _shop(db, beta.id, "beta")
    _event(db, test_app.id, "a-install", "installed", start + timedelta(days=1))
    _event(
        db, test_app.id, "a-uninstall", "uninstalled", start + timedelta(days=2)
    )
    _event(db, beta.id, "b-install", "reinstalled", start + timedelta(days=3))
    _event(
        db,
        test_app.id,
        "a-previous",
        "installed",
        previous_start + timedelta(days=1),
    )
    _subscription(
        db,
        test_app.id,
        "a-sub",
        "alpha",
        Decimal("20"),
        start + timedelta(days=1),
    )
    _transaction(
        db, test_app.id, "a-cash", Decimal("19.40"), start + timedelta(days=4)
    )
    _transaction(
        db,
        test_app.id,
        "a-previous-cash",
        Decimal("9.70"),
        previous_start + timedelta(days=4),
    )
    db.commit()

    report = build_period_report(
        db,
        [test_app, beta],
        start,
        end,
        previous_start,
        start,
        Scope.all(),
    )

    alpha = next(row for row in report.rows if row.app.slug == "test-app")
    assert (alpha.installs, alpha.uninstalls, alpha.net_installs) == (1, 1, 0)
    assert alpha.mrr_gained == Decimal("20")
    assert alpha.mrr_lost == Decimal("0")
    assert alpha.net_mrr == Decimal("20")
    assert alpha.collected == Decimal("19.40")
    assert report.totals.installs == sum(row.installs for row in report.rows)
    assert report.totals.net_mrr == sum(row.net_mrr for row in report.rows)
    assert report.totals.collected == sum(row.collected for row in report.rows)
    assert report.comparison["installs"]["change"] == 1
    assert report.comparison["collected"]["change"] == Decimal("9.70")


def test_report_keeps_zero_rows_and_scope_selects_one_app(
    db, app_factory, test_app
):
    beta = app_factory(slug="beta", name="Beta")
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 11, tzinfo=timezone.utc)

    combined = build_period_report(
        db, [test_app, beta], start, end, start - (end - start), start
    )
    scoped = build_period_report(
        db,
        [test_app, beta],
        start,
        end,
        start - (end - start),
        start,
        Scope.for_app(beta.id),
    )

    assert {row.app.slug for row in combined.rows} == {"test-app", "beta"}
    assert [row.app.slug for row in scoped.rows] == ["beta"]


def test_report_excludes_free_and_current_trial_mrr(db, test_app):
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(days=10), now
    for gid in ("paid", "free", "trial"):
        _shop(db, test_app.id, gid)
    _subscription(db, test_app.id, "paid-sub", "paid", Decimal("10"), start)
    _subscription(db, test_app.id, "free-sub", "free", Decimal("0"), start)
    _subscription(db, test_app.id, "trial-sub", "trial", Decimal("30"), start)
    db.execute(
        """insert into active_subscriptions
               (app_id, shop_gid, legacy_subscription_id, trial_ends_at, observed_at)
           values (%s, 'trial', 'trial-sub', %s, %s)""",
        (test_app.id, now + timedelta(days=5), now),
    )
    db.commit()

    report = build_period_report(
        db, [test_app], start, end, start - (end - start), start
    )

    assert report.rows[0].mrr_gained == Decimal("10")


def test_refunds_reduce_collected_cash_and_rows_sort_stably(
    db, app_factory, test_app
):
    beta = app_factory(slug="beta", name="Beta")
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 11, tzinfo=timezone.utc)
    _transaction(
        db,
        test_app.id,
        "refund",
        Decimal("-9.70"),
        start + timedelta(days=1),
        kind="AppSaleCredit",
    )
    _transaction(
        db, beta.id, "sale", Decimal("19.40"), start + timedelta(days=1)
    )
    db.commit()
    report = build_period_report(
        db, [test_app, beta], start, end, start - (end - start), start
    )

    descending = sort_rows(report.rows, "collected", "desc")
    ascending = sort_rows(report.rows, "collected", "asc")

    assert [row.app.slug for row in descending] == ["beta", "test-app"]
    assert [row.app.slug for row in ascending] == ["test-app", "beta"]
    assert next(row for row in report.rows if row.app == test_app).collected == Decimal(
        "-9.70"
    )
    assert sort_rows(report.rows, "unknown", "sideways") == sort_rows(
        report.rows, "net_mrr", "desc"
    )
