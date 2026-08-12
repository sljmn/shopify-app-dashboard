from datetime import date, datetime, timezone
from decimal import Decimal

from app_dashboard.payouts import payout_report
from app_dashboard.scope import Scope


def _earning(db, app_id, event_id, *, settlement_date, net, currency="USD"):
    db.execute(
        """
        insert into payout_earnings (
            app_id, id, event_type, earning_type, occurred_at,
            settlement_date, gross_amount, shopify_fee, net_amount, currency_code
        ) values (%s, %s, 'EARNING_CHARGE_RECURRING', 'APP_SALE',
                  %s, %s, %s, 0, %s, %s)
        """,
        (app_id, event_id, datetime(2026, 8, 1, tzinfo=timezone.utc),
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

    report = payout_report(db, Scope.all(), date(2026, 8, 1), date(2026, 8, 12), date(2026, 8, 8))

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

    report = payout_report(db, Scope.for_app(first.id), date(2026, 8, 1), date(2026, 8, 12))

    assert report.totals[0].net_amount == Decimal("8.00")
    assert report.settlements[0].earnings == 1
    assert report.unsettled == 0
