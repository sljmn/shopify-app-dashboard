from datetime import datetime, timezone
from decimal import Decimal

from app_dashboard.aso import (
    keyword_report,
    keyword_research,
    listing_history,
    opportunity_score,
    portfolio_report,
)
from psycopg.types.json import Jsonb
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


def test_listing_history_and_research_cross_reference_owned_data(db, test_app):
    snapshot = db.execute(
        """insert into aso_listing_snapshots
           (app_id,locale,captured_at,content_hash,listing)
           values (%s,'en',now(),'hash',%s) returning id""",
        (test_app.id, Jsonb({"name": "VAT exemption", "description": "EU VAT"})),
    ).fetchone()[0]
    db.execute(
        """insert into aso_listing_changes
           (app_id,snapshot_id,locale,changed_at,field,before_value,after_value)
           values (%s,%s,'en',now(),'name',%s,%s)""",
        (test_app.id, snapshot, Jsonb("Old"), Jsonb("VAT exemption")),
    )
    db.execute(
        """insert into aso_popular_keywords
           (keyword,source,first_seen_at,last_seen_at)
           values ('vat exemption','autocomplete',now(),now())"""
    )
    _seed(db, test_app.id, "2026-08-01", "vat exemption", 2, 1, 5)
    assert listing_history(db, test_app.id)[0].field == "name"
    research = keyword_research(db, test_app.id)
    assert research[0].in_listing is True
    assert research[0].in_traffic is True
