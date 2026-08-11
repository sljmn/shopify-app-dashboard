from datetime import date

from app_dashboard.ga4 import sync_ga4, upsert_rows


def _row(sessions):
    return {
        "date": "2026-08-01",
        "dimension": "total",
        "value": "",
        "sessions": sessions,
        "users": sessions - 1,
        "add_app_clicks": 2,
        "installs": 1,
        "ad_clicks": 0,
    }


def test_identical_ga4_keys_are_isolated_per_app(db, test_app, app_factory):
    other = app_factory(slug="other-app", name="Other App")

    assert upsert_rows(db, test_app.id, [_row(10)]) == 1
    assert upsert_rows(db, other.id, [_row(20)]) == 1
    upsert_rows(db, test_app.id, [_row(15)])

    assert db.execute(
        "select app_id, sessions from ga4_daily order by app_id"
    ).fetchall() == [(test_app.id, 15), (other.id, 20)]


def test_force_full_ga4_sync_ignores_existing_rows(db, test_app, monkeypatch):
    upsert_rows(db, test_app.id, [_row(10)])
    seen = {}

    def fetch(client, property_id, start, end):
        seen.update(start=start, end=end)
        return []

    monkeypatch.setattr("app_dashboard.ga4.fetch_rows", fetch)
    sync_ga4(
        db, object(), test_app, today=date(2026, 8, 11),
        earliest=date(2025, 1, 1), force_full=True,
    )

    assert seen == {"start": date(2025, 1, 1), "end": date(2026, 8, 11)}
