from datetime import date, datetime, timezone
from decimal import Decimal

from app_dashboard.payouts import payout_report
from app_dashboard.scope import Scope


def _earning(
    db, app_id, event_id, *, settlement_date, net, currency="USD",
    occurred_at=None,
):
    db.execute(
        """
        insert into payout_earnings (
            app_id, id, event_type, earning_type, occurred_at,
            settlement_date, gross_amount, shopify_fee, net_amount, currency_code
        ) values (%s, %s, 'EARNING_CHARGE_RECURRING', 'APP_SALE',
                  %s, %s, %s, 0, %s, %s)
        """,
        (app_id, event_id, occurred_at or datetime(2026, 8, 1, tzinfo=timezone.utc),
         settlement_date, Decimal(net), Decimal(net), currency),
    )


def test_payout_report_groups_by_settlement_date_and_currency(db, app_factory):
    first = app_factory(slug="first", name="First")
    second = app_factory(slug="second", name="Second")
    _earning(db, first.id, "one", settlement_date=date(2026, 8, 8), net="8.25")
    _earning(db, first.id, "two", settlement_date=date(2026, 8, 8), net="1.75")
    _earning(db, second.id, "three", settlement_date=date(2026, 8, 8), net="7.00", currency="EUR")
    _earning(db, second.id, "waiting", settlement_date=None, net="4.00")
    _earning(db, first.id, "old", settlement_date=date(2025, 1, 1), net="99.00")

    report = payout_report(
        db, Scope.all(), date(2026, 8, 1), date(2026, 8, 12),
        date(2026, 8, 8), today=date(2026, 8, 12),
    )

    assert [(row.currency_code, row.net_amount) for row in report.totals] == [
        ("EUR", Decimal("7.00")), ("USD", Decimal("10.00")),
    ]
    assert [(row.currency_code, row.earnings) for row in report.settlements] == [("EUR", 1), ("USD", 2)]
    assert {row.app_name for row in report.earnings} == {"First", "Second"}
    assert report.unsettled == 1


def test_payout_report_honours_app_scope(db, app_factory):
    first = app_factory(slug="first", name="First")
    second = app_factory(slug="second", name="Second")
    _earning(db, first.id, "one", settlement_date=date(2026, 8, 8), net="8.00")
    _earning(db, second.id, "two", settlement_date=date(2026, 8, 8), net="3.00")
    _earning(db, second.id, "waiting", settlement_date=None, net="2.00")

    report = payout_report(
        db, Scope.for_app(first.id), date(2026, 8, 1), date(2026, 8, 12),
        today=date(2026, 8, 12),
    )

    assert report.totals[0].net_amount == Decimal("8.00")
    assert report.settlements[0].earnings == 1
    assert report.unsettled == 0


def test_cashflow_uses_mantle_half_month_windows_and_statuses(db, app_factory):
    app = app_factory(slug="cashflow", name="Cashflow")
    _earning(
        db, app.id, "paid", settlement_date=date(2026, 8, 1), net="120.00",
        occurred_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
    )
    _earning(
        db, app.id, "due", settlement_date=date(2026, 8, 16), net="80.00",
        occurred_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
    )
    _earning(
        db, app.id, "billed", settlement_date=None, net="25.00",
        occurred_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )

    report = payout_report(
        db, Scope.for_app(app.id), date(2026, 7, 1), date(2026, 8, 31),
        today=date(2026, 8, 12),
    )

    previous, current, upcoming = report.cashflow
    assert (previous.start, previous.end, previous.payment_date) == (
        date(2026, 7, 16), date(2026, 7, 31), date(2026, 8, 6),
    )
    assert previous.paid == Decimal("120.00")
    assert previous.due == Decimal("0")
    assert (current.start, current.end, current.payment_date) == (
        date(2026, 8, 1), date(2026, 8, 15), date(2026, 8, 20),
    )
    assert current.due == Decimal("80.00")
    assert current.confirmed == Decimal("80.00")
    assert current.estimated is True
    assert upcoming.billed == Decimal("25.00")
    assert upcoming.estimated is True
    assert report.cashflow_currency == "USD"
    assert report.cashflow_max >= max(row.total for row in report.cashflow)


def test_upcoming_projection_respects_monthly_and_annual_billing_cadence(
    db, app_factory,
):
    app = app_factory(slug="cadence", name="Cadence")
    for shop_gid, sub_id, monthly, interval, paid_on, gross, net in (
        ("monthly", "monthly-sub", "20", "EVERY_30_DAYS", "2026-07-20", "20", "19.42"),
        ("annual-due", "annual-due-sub", "10", "ANNUAL", "2025-08-20", "120", "116.52"),
        ("annual-later", "annual-later-sub", "10", "ANNUAL", "2025-10-20", "120", "116.52"),
    ):
        db.execute(
            "insert into shops (app_id, shop_gid, install_state) values (%s,%s,'installed')",
            (app.id, shop_gid),
        )
        db.execute(
            """insert into subscriptions
               (app_id, id, shop_gid, monthly_amount, billing_type, converted_at)
               values (%s,%s,%s,%s,%s,%s::timestamptz)""",
            (app.id, sub_id, shop_gid, monthly, interval, paid_on),
        )
        db.execute(
            """insert into transactions
               (app_id, id, type, created_at, shop_gid, billing_interval,
                gross_amount, net_amount, currency_code)
               values (%s,%s,'AppSubscriptionSale',%s::timestamptz,%s,%s,%s,%s,'USD')""",
            (app.id, f"tx-{sub_id}", paid_on, shop_gid, interval, gross, net),
        )

    report = payout_report(
        db, Scope.for_app(app.id), date(2026, 7, 1), date(2026, 8, 31),
        today=date(2026, 8, 12),
    )

    # The next window is 16-31 Aug: monthly and the Aug annual renewal are
    # included. The October annual subscription is not spread over every month.
    assert report.cashflow[-1].upcoming == Decimal("135.94")


def test_current_window_projects_only_the_unconfirmed_remainder(db, app_factory):
    app = app_factory(slug="current", name="Current")
    db.execute(
        "insert into shops (app_id, shop_gid, install_state) values (%s,'shop','installed')",
        (app.id,),
    )
    db.execute(
        """insert into subscriptions
           (app_id, id, shop_gid, monthly_amount, billing_type, converted_at)
           values (%s,'sub','shop',20,'EVERY_30_DAYS','2026-07-10')""",
        (app.id,),
    )
    db.execute(
        """insert into transactions
           (app_id, id, type, created_at, shop_gid, billing_interval,
            gross_amount, net_amount, currency_code)
           values (%s,'tx','AppSubscriptionSale','2026-07-10','shop',
                   'EVERY_30_DAYS',20,19.42,'USD')""",
        (app.id,),
    )
    _earning(
        db, app.id, "confirmed", settlement_date=date(2026, 8, 16),
        net="10.00", occurred_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )

    report = payout_report(
        db, Scope.for_app(app.id), date(2026, 8, 1), date(2026, 8, 31),
        today=date(2026, 8, 12),
    )

    current = report.cashflow[1]
    assert current.confirmed == Decimal("10.00")
    assert current.upcoming == Decimal("9.42")
    assert current.total == Decimal("19.42")
