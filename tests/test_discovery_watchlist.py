from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app_dashboard.app_store_discovery import (
    CategoryApp,
    CategoryResult,
    SitemapApp,
    sync_discovered_apps,
    sync_discovery_categories,
)
from app_dashboard.discovery_watchlist import (
    follow_app,
    follow_automatic_candidates,
    listing_versions,
    store_competitor_snapshot,
    unfollow_app,
    watchlist_summary,
    watch_status,
)

NOW = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)


def test_manual_follow_unfollow_and_refollow_are_idempotent(db):
    sync_discovered_apps(db, [SitemapApp("alpha", date(2026, 8, 1))], NOW)
    first = follow_app(db, "alpha", source="manual", now=NOW)
    again = follow_app(db, "alpha", source="manual", now=NOW + timedelta(hours=1))
    assert first.followed_at == again.followed_at == NOW
    assert db.execute("select count(*) from discovery_watchlist").fetchone()[0] == 1

    stopped = unfollow_app(db, "alpha", now=NOW + timedelta(days=1))
    assert stopped.active is False
    resumed = follow_app(db, "alpha", source="manual", now=NOW + timedelta(days=2))
    assert resumed.active is True
    assert resumed.followed_at == NOW
    assert resumed.unfollowed_at is None


def test_follow_validates_source_and_handle(db):
    with pytest.raises(ValueError, match="invalid-follow-source"):
        follow_app(db, "missing", source="magic")
    with pytest.raises(LookupError, match="unknown-discovered-app"):
        follow_app(db, "missing", source="manual")
    with pytest.raises(LookupError, match="app-not-followed"):
        unfollow_app(db, "missing")


def test_automatic_follow_adds_gems_and_contenders_once(db):
    day1 = NOW
    day8 = day1 + timedelta(days=7)
    sync_discovered_apps(db, [SitemapApp("baseline", None)], day1)
    sync_discovery_categories(db, [CategoryResult("design", "Design", (
        CategoryApp("baseline", "Baseline", 100, Decimal("4.5"), 2),
    ))], day1)
    sync_discovered_apps(db, [
        SitemapApp("baseline", None), SitemapApp("gem", None),
        SitemapApp("quiet", None),
    ], day1 + timedelta(days=1))
    sync_discovery_categories(db, [CategoryResult("design", "Design", (
        CategoryApp("baseline", "Baseline", 100, Decimal("4.5"), 2),
        CategoryApp("gem", "Gem", 10, Decimal("4.9"), 10),
        CategoryApp("quiet", "Quiet", 2, Decimal("5.0"), 12),
    ))], day1 + timedelta(days=1))
    sync_discovery_categories(db, [CategoryResult("design", "Design", (
        CategoryApp("baseline", "Baseline", 150, Decimal("4.6"), 1),
        CategoryApp("gem", "Gem", 20, Decimal("4.9"), 5),
        CategoryApp("quiet", "Quiet", 2, Decimal("5.0"), 12),
    ))], day8)

    assert follow_automatic_candidates(db, now=day8) == {
        "followed": 2, "already_followed": 0,
    }
    assert watch_status(db, "gem").follow_source == "rising_gem"
    assert watch_status(db, "quiet").follow_source == "new_contender"
    assert watch_status(db, "baseline") is None
    assert follow_automatic_candidates(db, now=day8) == {
        "followed": 0, "already_followed": 2,
    }

    unfollow_app(db, "quiet", now=day8)
    follow_automatic_candidates(db, now=day8)
    assert watch_status(db, "quiet").active is False


def test_weekly_summary_and_listing_history_use_period_boundaries(db):
    before = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
    after = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)
    sync_discovered_apps(db, [SitemapApp("alpha", None)], before)
    sync_discovery_categories(db, [CategoryResult("design", "Design", (
        CategoryApp("alpha", "Alpha", 10, Decimal("4.8"), 10),
    ))], before)
    app_id = db.execute(
        "select id from discovered_apps where handle='alpha'"
    ).fetchone()[0]
    followed_at = datetime(2026, 8, 5, 8, tzinfo=timezone.utc)
    follow_app(db, "alpha", source="manual", now=followed_at)
    store_competitor_snapshot(
        db, app_id, {"name": "Alpha", "description": "Before"}, (),
        followed_at,
    )
    changed_at = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)
    store_competitor_snapshot(
        db, app_id, {"name": "Alpha", "description": "After"}, (),
        changed_at,
    )
    sync_discovery_categories(db, [CategoryResult("design", "Design", (
        CategoryApp("alpha", "Alpha", 20, Decimal("4.9"), 4),
    ))], after)

    summary = watchlist_summary(db, date(2026, 8, 5), date(2026, 8, 12))
    assert summary["new_follows"][0][:3] == ("alpha", "Alpha", "manual")
    assert summary["review_gainers"] == [("alpha", "Alpha", 10)]
    assert summary["rank_gainers"] == [("alpha", "Alpha", 6)]
    assert summary["patterns"] == [("description", 1)]
    versions = listing_versions(db, app_id)
    assert versions[0]["review_movement"] == 10
    assert versions[0]["rank_movement"] == 6
