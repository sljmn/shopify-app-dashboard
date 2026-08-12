from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app_dashboard.app_store_discovery import (
    CategoryApp,
    CategoryResult,
    SitemapApp,
    CATEGORY_SITEMAP_URL,
    collect_categories,
    discovery_report,
    growth_signals,
    parse_app_sitemap,
    parse_category_page,
    parse_category_sitemap,
    sync_discovered_apps,
    sync_discovery_categories,
)


def sitemap(*urls):
    body = "".join(
        f"<url><loc>{url}</loc><lastmod>2026-08-12</lastmod></url>" for url in urls
    )
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'


def test_parses_canonical_apps_and_categories_only():
    apps = parse_app_sitemap(sitemap(
        "https://apps.shopify.com/book-importer",
        "https://apps.shopify.com/book-importer",
        "https://apps.shopify.com/categories",
        "https://example.com/not-shopify",
    ))
    assert apps == [SitemapApp("book-importer", date(2026, 8, 12))]
    assert parse_category_sitemap(sitemap(
        "https://apps.shopify.com/categories/store-design",
        "https://apps.shopify.com/categories/store-design-product-display",
        "https://apps.shopify.com/book-importer",
    )) == ["store-design", "store-design-product-display"]


def test_category_cards_supply_names_and_are_deduplicated():
    html = """
      <title>Best Product display Apps For 2026 - Shopify App Store</title>
      <div data-controller="app-card" data-app-card-handle-value="alpha"
           data-app-card-name-value="Alpha App"></div>
      <div data-controller="app-card" data-app-card-handle-value="alpha"
           data-app-card-name-value="Alpha Again"></div>
      <div data-controller="app-card" data-app-card-handle-value="beta"
           data-app-card-name-value="Beta"></div>
    """
    name, apps = parse_category_page(html, "product-display")
    assert name == "Product display"
    assert apps == [CategoryApp("alpha", "Alpha Again"), CategoryApp("beta", "Beta")]


def test_category_cards_parse_review_count_and_rating():
    html = """
      <div data-controller="app-card" data-app-card-handle-value="alpha"
           data-app-card-name-value="Alpha App">
        <span>4.8 out of 5 stars</span><span>1,234 total reviews</span>
      </div>
    """
    _, apps = parse_category_page(html, "design")
    assert apps == [CategoryApp("alpha", "Alpha App", 1234, Decimal("4.8"))]


def test_category_collection_uses_every_sitemap_category_and_stops_on_empty_page():
    class Response:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

        def raise_for_status(self):
            pass

    category_xml = sitemap(
        "https://apps.shopify.com/categories/design",
        "https://apps.shopify.com/categories/marketing",
        "https://apps.shopify.com/categories/sales-channels",
    )
    card = ('<title>Best {name} Apps For 2026</title>'
            '<div data-controller="app-card" data-app-card-handle-value="{handle}" '
            'data-app-card-name-value="{name} App"></div>')
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if url == CATEGORY_SITEMAP_URL:
            return Response(category_xml)
        if "page=1" in url and "/design/" in url:
            return Response(card.format(name="Design", handle="design-app"))
        if "page=1" in url and "/marketing/" in url:
            return Response(card.format(name="Marketing", handle="marketing-app"))
        if "/sales-channels/" in url:
            return Response("not found", 404)
        return Response("<title>Empty</title>")

    result = collect_categories(get, sleep=lambda *_: None, max_pages=5, page_delay=0)
    assert [category.slug for category in result] == ["design", "marketing"]
    assert result[0].apps[0].rank == 1
    assert len(calls) == 6  # two leaf categories paginate; the umbrella stops at 404


def test_initial_import_is_baseline_and_later_handle_is_new(db):
    first = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)
    result = sync_discovered_apps(db, [SitemapApp("alpha", date(2026, 8, 1))], first)
    assert result == {"seen": 1, "new": 0, "baseline": True}

    later = first + timedelta(days=1)
    result = sync_discovered_apps(db, [
        SitemapApp("alpha", date(2026, 8, 2)),
        SitemapApp("beta", date(2026, 8, 12)),
    ], later)
    assert result == {"seen": 2, "new": 1, "baseline": False}
    rows = db.execute(
        "select handle,first_seen_at,is_baseline from discovered_apps order by handle"
    ).fetchall()
    assert rows == [("alpha", first, True), ("beta", later, False)]


def test_empty_import_does_not_complete_baseline(db):
    with pytest.raises(ValueError, match="empty-app-sitemap"):
        sync_discovered_apps(db, [])
    assert db.execute("select count(*) from discovery_state").fetchone()[0] == 0


def test_category_sync_records_all_memberships_without_duplicate_apps(db):
    sync_discovered_apps(
        db, [SitemapApp("alpha", None), SitemapApp("beta", None)],
        datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    result = sync_discovery_categories(db, [
        CategoryResult("design", "Design", (
            CategoryApp("alpha", "Alpha"), CategoryApp("beta", "Beta"),
        )),
        CategoryResult("marketing", "Marketing", (CategoryApp("alpha", "Alpha"),)),
    ], datetime(2026, 8, 12, tzinfo=timezone.utc))
    assert result == {"categories": 2, "memberships": 3}
    assert db.execute("select count(*) from discovered_apps").fetchone()[0] == 2
    assert db.execute("select count(*) from discovered_app_categories").fetchone()[0] == 3

    with pytest.raises(ValueError, match="empty-category-crawl"):
        sync_discovery_categories(db, [])
    assert db.execute("select count(*) from discovered_app_categories").fetchone()[0] == 3

    sync_discovery_categories(db, [
        CategoryResult("design", "Design", (CategoryApp("beta", "Beta"),)),
    ], datetime(2026, 8, 13, tzinfo=timezone.utc))
    # Design is refreshed exactly; Marketing is retained because it was absent
    # from this crawl and could have been a partial source response.
    memberships = db.execute(
        """select category.slug,app.handle from discovered_app_categories member
           join discovery_categories category on category.id=member.category_id
           join discovered_apps app on app.id=member.discovered_app_id
           order by category.slug,app.handle"""
    ).fetchall()
    assert memberships == [("design", "beta"), ("marketing", "alpha")]


def test_category_sync_records_daily_review_and_rank_observations(db):
    observed_at = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)
    sync_discovered_apps(db, [SitemapApp("alpha", None)], observed_at)
    sync_discovery_categories(db, [
        CategoryResult("design", "Design", (
            CategoryApp("alpha", "Alpha", 12, Decimal("4.7"), 8),
        )),
        CategoryResult("marketing", "Marketing", (
            CategoryApp("alpha", "Alpha", 12, Decimal("4.7"), 3),
        )),
    ], observed_at)

    app_observation = db.execute(
        """select review_count,rating,best_category_rank
           from discovery_app_observations"""
    ).fetchone()
    assert app_observation == (12, Decimal("4.70"), 3)
    assert db.execute(
        "select position from discovery_category_observations order by position"
    ).fetchall() == [(3,), (8,)]


def test_growth_signals_separate_baseline_growers_and_new_contenders(db):
    day1 = datetime(2026, 7, 1, 8, tzinfo=timezone.utc)
    day8 = day1 + timedelta(days=7)
    sync_discovered_apps(db, [SitemapApp("established", None)], day1)
    sync_discovery_categories(db, [
        CategoryResult("design", "Design", (
            CategoryApp("established", "Established", 100, Decimal("4.5"), 2),
        )),
    ], day1)

    sync_discovered_apps(db, [
        SitemapApp("established", None), SitemapApp("young-gem", None),
        SitemapApp("young-quiet", None),
    ], day1 + timedelta(days=1))
    sync_discovery_categories(db, [
        CategoryResult("design", "Design", (
            CategoryApp("established", "Established", 100, Decimal("4.5"), 2),
            CategoryApp("young-gem", "Young Gem", 10, Decimal("4.9"), 10),
            CategoryApp("young-quiet", "Young Quiet", 2, Decimal("5.0"), 12),
        )),
    ], day1 + timedelta(days=1))
    sync_discovery_categories(db, [
        CategoryResult("design", "Design", (
            CategoryApp("established", "Established", 150, Decimal("4.6"), 1),
            CategoryApp("young-gem", "Young Gem", 20, Decimal("4.9"), 5),
            CategoryApp("young-quiet", "Young Quiet", 2, Decimal("5.0"), 12),
        )),
    ], day8)

    signals = growth_signals(db, now=day8)
    assert [row["handle"] for row in signals["fastest"]] == [
        "established", "young-gem",
    ]
    assert [row["handle"] for row in signals["gems"]] == ["young-gem"]
    assert {row["handle"] for row in signals["contenders"]} == {
        "young-gem", "young-quiet",
    }
    gem = signals["gems"][0]
    assert gem["previous_delta"] == 10
    assert gem["delta7"] is None
    assert gem["rank_change"] == 5


def test_report_excludes_baseline_and_counts_each_new_app_once(db):
    baseline = datetime(2026, 7, 1, tzinfo=timezone.utc)
    sync_discovered_apps(db, [SitemapApp("old", None)], baseline)
    monday = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)
    sync_discovered_apps(db, [
        SitemapApp("old", None), SitemapApp("new-app", date(2026, 8, 10)),
    ], monday)
    sync_discovery_categories(db, [
        CategoryResult("design", "Design", (CategoryApp("new-app", "New App"),)),
        CategoryResult("marketing", "Marketing", (CategoryApp("new-app", "New App"),)),
    ], monday)

    report = discovery_report(
        db, search="new", category="design", now=datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
    )
    assert report["indexed"] == 2
    assert report["new_this_week"] == 1
    assert report["new_last_7_days"] == 1
    assert report["total"] == 1
    assert sum(week["count"] for week in report["weeks"]) == 1
    assert report["rows"][0][0:2] == ("new-app", "New App")
