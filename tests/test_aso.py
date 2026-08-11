from datetime import datetime, timezone
from decimal import Decimal

from app_dashboard.aso import keyword_report, opportunity_score, portfolio_report
from app_dashboard.periods import resolve_period


NOW = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)


def _period():
    return resolve_period("30d", None, None, now=NOW)


def _seed(db, app_id, on, keyword, users, clicks, position):
    db.execute(
        """
        insert into aso_keyword_daily
            (app_id, date, keyword, search_type, users, install_clicks,
             average_position, latest_position, position_samples)
        values (%s,%s,%s,'search',%s,%s,%s,%s,1)
        """,
        (app_id, on, keyword, users, clicks, position, position),
    )


def test_keyword_report_compares_previous_position(db, test_app):
    _seed(db, test_app.id, "2026-08-01", "vat exemption", 20, 4, 8)
    _seed(db, test_app.id, "2026-07-01", "vat exemption", 10, 1, 12)
    report = keyword_report(db, test_app.id, _period())
    assert report.rows[0].keyword == "vat exemption"
    assert report.rows[0].position_change == 4
    assert report.rows[0].conversion_pct == 20.0
    assert report.totals.users == 20


def test_opportunity_score_is_bounded_and_transparent():
    assert opportunity_score(0, 20) == 0
    assert opportunity_score(5, 1) == 0
    assert opportunity_score(5, 20) > 0
    assert 0 <= opportunity_score(999, 999) <= 100


def test_portfolio_keeps_apps_without_ga4_rows(db, test_app, app_factory):
    other = app_factory(slug="other", name="Other")
    _seed(db, test_app.id, "2026-08-01", "vat exemption", 20, 4, Decimal("8"))
    rows = portfolio_report(db, [test_app, other], _period())
    assert {row.app.slug for row in rows} == {"test-app", "other"}
    assert next(row for row in rows if row.app == other).status == "not_configured"
