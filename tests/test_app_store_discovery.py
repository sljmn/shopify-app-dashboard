from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

from app_dashboard.app_store_discovery import (
    CategoryApp,
    CategoryResult,
    SitemapApp,
    CATEGORY_SITEMAP_URL,
    collect_categories,
    category_opportunities,
    category_dashboard,
    discovery_report,
    growth_signals,
    parse_app_sitemap,
    parse_category_page,
    parse_category_sitemap,
    pricing_profile,
    search_app_catalog,
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


def test_category_cards_capture_public_icon_url():
    html = """
      <div data-controller="app-card" data-app-card-handle-value="alpha"
           data-app-card-name-value="Alpha App"
           data-app-card-icon-url-value="https://cdn.shopify.com/app/icon.png">
      </div>
    """
    _, apps = parse_category_page(html, "design")
    assert apps == [CategoryApp(
        "alpha", "Alpha App", icon_url="https://cdn.shopify.com/app/icon.png",
    )]


def test_category_cards_detect_shopifys_official_bfs_badge():
    html = """
      <div data-controller="app-card" data-app-card-handle-value="alpha"
           data-app-card-name-value="Alpha App">
        <span class="built-for-shopify-badge">Built for Shopify</span>
      </div>
      <div data-controller="app-card" data-app-card-handle-value="beta"
           data-app-card-name-value="Beta App"></div>
    """
    _, apps = parse_category_page(html, "design")
    assert apps[0].built_for_shopify is True
    assert apps[1].built_for_shopify is False


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


def test_sitemap_sync_separates_updates_delisting_and_relisting(db):
    day1 = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
    sync_discovered_apps(db, [SitemapApp("alpha", date(2026, 8, 1))], day1)

    day2 = day1 + timedelta(days=1)
    sync_discovered_apps(db, [
        SitemapApp("alpha", date(2026, 8, 2)),
        SitemapApp("beta", date(2026, 8, 2)),
    ], day2)
    assert db.execute(
        "select event_type from discovery_app_events order by id"
    ).fetchall() == [("discovered",), ("listing_updated",)]
    assert db.execute(
        """select watch.follow_source,watch.active
           from discovery_watchlist watch join discovered_apps app
             on app.id=watch.discovered_app_id where app.handle='beta'"""
    ).fetchone() == ("new_app", True)

    sync_discovered_apps(
        db, [SitemapApp("alpha", date(2026, 8, 2))], day2 + timedelta(days=1)
    )
    sync_discovered_apps(
        db, [SitemapApp("alpha", date(2026, 8, 2))], day2 + timedelta(days=2)
    )
    assert db.execute(
        "select delisted_at,missing_scan_count from discovered_apps where handle='beta'"
    ).fetchone() == (None, 2)

    delisted_at = day2 + timedelta(days=3)
    sync_discovered_apps(
        db, [SitemapApp("alpha", date(2026, 8, 2))], delisted_at
    )
    assert db.execute(
        "select delisted_at,missing_scan_count from discovered_apps where handle='beta'"
    ).fetchone() == (delisted_at, 3)

    relisted_at = day2 + timedelta(days=4)
    sync_discovered_apps(db, [
        SitemapApp("alpha", date(2026, 8, 2)),
        SitemapApp("beta", date(2026, 8, 2)),
    ], relisted_at)
    assert db.execute(
        "select delisted_at,missing_scan_count from discovered_apps where handle='beta'"
    ).fetchone() == (None, 0)
    assert db.execute(
        """select event_type from discovery_app_events event
           join discovered_apps app on app.id=event.discovered_app_id
           where app.handle='beta' order by event.id"""
    ).fetchall() == [("discovered",), ("delisted",), ("relisted",)]


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
    assert result == {"categories": 2, "memberships": 3, "new": 0}
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


def test_category_sync_records_current_bfs_status_and_check_time(db):
    observed_at = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)
    sync_discovered_apps(
        db, [SitemapApp("alpha", None), SitemapApp("beta", None)], observed_at,
    )
    sync_discovery_categories(db, [CategoryResult("design", "Design", (
        CategoryApp("alpha", "Alpha", built_for_shopify=True),
        CategoryApp("beta", "Beta", built_for_shopify=False),
    ))], observed_at)
    assert db.execute(
        """select handle,built_for_shopify,bfs_checked_at from discovered_apps
           order by handle"""
    ).fetchall() == [
        ("alpha", True, observed_at), ("beta", False, observed_at),
    ]


def test_category_sync_keeps_latest_public_icon_url(db):
    observed_at = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)
    sync_discovered_apps(db, [SitemapApp("alpha", None)], observed_at)
    sync_discovery_categories(db, [CategoryResult("design", "Design", (
        CategoryApp(
            "alpha", "Alpha", icon_url="https://cdn.shopify.com/alpha.png",
        ),
    ))], observed_at)
    assert db.execute(
        "select icon_url from discovered_apps where handle='alpha'"
    ).fetchone()[0] == "https://cdn.shopify.com/alpha.png"


def test_category_crawl_can_be_the_first_source_of_a_new_app(db):
    baseline_at = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)
    sync_discovered_apps(db, [SitemapApp("alpha", None)], baseline_at)

    found_at = baseline_at + timedelta(hours=2)
    result = sync_discovery_categories(db, [
        CategoryResult("design", "Design", (
            CategoryApp("alpha", "Alpha", 12, Decimal("4.7"), 1),
            CategoryApp("beta", "Beta", 0, None, 2),
        )),
    ], found_at)

    assert result["new"] == 1
    assert db.execute(
        """select app.handle,event.event_type,event.details->>'source'
           from discovery_app_events event join discovered_apps app
             on app.id=event.discovered_app_id"""
    ).fetchall() == [("beta", "discovered", "shopify_category_crawl")]

    # The complete sitemap sees an existing handle and must not create a second
    # discovery event or reinterpret Shopify's lastmod as its launch date.
    sync_discovered_apps(db, [
        SitemapApp("alpha", None), SitemapApp("beta", date(2026, 8, 12)),
    ], found_at + timedelta(hours=1))
    assert db.execute(
        "select count(*) from discovery_app_events where event_type='discovered'"
    ).fetchone()[0] == 1


def test_pricing_profile_separates_free_monthly_and_annual_prices():
    profile = pricing_profile({"pricing": [
        "Free plan available",
        "Pro $19 / month or $190/year and save 17%",
        "7-day free trial",
    ]})
    assert profile["label"] == "Free + paid"
    assert profile["monthly"] == [Decimal("19")]
    assert profile["annual"] == [Decimal("190")]
    assert profile["trial"] == "7-day trial"


def test_category_opportunities_expose_coverage_instead_of_guessing_prices(db):
    observed_at = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)
    sync_discovered_apps(db, [
        SitemapApp("alpha", date(2025, 1, 1)),
        SitemapApp("beta", date(2026, 8, 1)),
    ], observed_at)
    sync_discovery_categories(db, [
        CategoryResult("design", "Design", (
            CategoryApp("alpha", "Alpha", 0, None, 1),
            CategoryApp("beta", "Beta", 10, Decimal("4.5"), 2),
        )),
    ], observed_at)
    app_id = db.execute(
        "select id from discovered_apps where handle='alpha'"
    ).fetchone()[0]
    db.execute(
        """insert into discovery_listing_snapshots
             (discovered_app_id,captured_at,content_hash,listing)
           values (%s,%s,%s,%s)""",
        (app_id, observed_at, "pricing-alpha", Jsonb({
            "pricing": ["Pro $24 / month or $240/year"],
        })),
    )

    row = category_opportunities(db, now=observed_at)[0]
    assert row["name"] == "Design"
    assert row["apps"] == 2
    assert row["reviews_covered"] == 2
    assert row["zero_review_share"] == 50
    assert row["pricing_covered"] == 1
    assert row["paid_share"] == 100
    assert row["average_monthly_price"] == Decimal("24")


def test_category_dashboard_starts_with_inventory_and_filters_signals(db):
    now = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)
    sync_discovered_apps(db, [
        SitemapApp("ranked", None), SitemapApp("unmeasured", None),
    ], now - timedelta(days=40))
    sync_discovery_categories(db, [CategoryResult("anti-theft", "Anti theft", (
        CategoryApp("ranked", "Ranked", 10, Decimal("4.8"), 1, True),
        CategoryApp("unmeasured", "Unmeasured", None, None, 2, False),
    ))], now - timedelta(days=30))
    sync_discovery_categories(db, [CategoryResult("anti-theft", "Anti theft", (
        CategoryApp("ranked", "Ranked", 15, Decimal("4.8"), 1, True),
        CategoryApp("unmeasured", "Unmeasured", None, None, 2, False),
    ))], now - timedelta(days=7))
    sync_discovery_categories(db, [CategoryResult("anti-theft", "Anti theft", (
        CategoryApp("ranked", "Ranked", 18, Decimal("4.9"), 1, True),
        CategoryApp("unmeasured", "Unmeasured", None, None, 2, False),
    ))], now)
    ranked_id = db.execute(
        "select id from discovered_apps where handle='ranked'"
    ).fetchone()[0]
    snapshot_id = db.execute(
        """insert into discovery_listing_snapshots
             (discovered_app_id,captured_at,content_hash,listing)
           values (%s,%s,'category-listing',%s) returning id""",
        (ranked_id, now, Jsonb({
            "developer": {"name": "Ranked Labs"},
            "pricing": ["Pro $12 / month"],
        })),
    ).fetchone()[0]

    report = category_dashboard(db, "anti-theft", now=now)

    assert report["category_name"] == "Anti theft"
    assert report["total_apps"] == 2
    assert report["measured_apps"] == 1
    assert report["bfs_apps"] == 1
    assert [row["handle"] for row in report["rows"]] == [
        "ranked", "unmeasured",
    ]
    assert report["rows"][0]["delta7"] == 3
    assert report["rows"][0]["delta30"] == 8
    assert report["rows"][0]["latest_delta"] == 3
    assert report["rows"][0]["developer"] == "Ranked Labs"
    assert report["rows"][0]["pricing"]["monthly"] == [Decimal("12")]
    assert report["rows"][1]["reviews"] is None
    assert [row["handle"] for row in category_dashboard(
        db, "anti-theft", signal="reviews", now=now,
    )["rows"]] == ["ranked"]
    assert category_dashboard(db, "anti-theft", bfs="bfs", now=now)[
        "total"
    ] == 1
    assert category_dashboard(
        db, "anti-theft", search="Ranked Labs", now=now,
    )["total"] == 1
    assert category_dashboard(
        db, "anti-theft", page=2, per_page=1, now=now,
    )["rows"][0]["handle"] == "unmeasured"

    db.execute(
        """insert into discovery_app_events
             (discovered_app_id,event_type,occurred_at)
           values (%s,'discovered',%s)""",
        (ranked_id, now - timedelta(days=1)),
    )
    db.execute(
        """insert into discovery_listing_changes
             (discovered_app_id,snapshot_id,changed_at,field,
              before_value,after_value)
           values (%s,%s,%s,'pricing',%s,%s)""",
        (ranked_id, snapshot_id, now - timedelta(days=1),
         Jsonb(["Free"]), Jsonb(["Pro $12 / month"])),
    )
    assert category_dashboard(
        db, "anti-theft", signal="new", now=now,
    )["rows"][0]["handle"] == "ranked"
    assert category_dashboard(
        db, "anti-theft", signal="listing", now=now,
    )["rows"][0]["handle"] == "ranked"

    db.execute(
        """update discovered_apps set delisted_at=%s
           where handle='unmeasured'""",
        (now,),
    )
    assert category_dashboard(db, "anti-theft", now=now)["total"] == 1
    assert category_dashboard(
        db, "anti-theft", signal="delisted", now=now,
    )["rows"][0]["handle"] == "unmeasured"
    assert category_dashboard(db, "missing", now=now) is None


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
    assert sum(week["new"] for week in report["weeks"]) == 1
    assert report["rows"][0]["handle"] == "new-app"
    assert report["rows"][0]["name"] == "New App"


def test_focused_discovery_reports_isolate_launches_and_expose_verified_diff(db):
    baseline_at = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
    now = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
    baseline_id = db.execute(
        """insert into discovered_apps
             (handle,display_name,first_seen_at,last_seen_at,is_baseline)
           values ('baseline-app','Baseline',%s,%s,true) returning id""",
        (baseline_at, now),
    ).fetchone()[0]
    launched_id = db.execute(
        """insert into discovered_apps
             (handle,display_name,first_seen_at,last_seen_at,is_baseline)
           values ('real-launch','Real Launch',%s,%s,false) returning id""",
        (now - timedelta(days=5), now),
    ).fetchone()[0]
    db.execute(
        """insert into discovery_app_events
             (discovered_app_id,event_type,occurred_at)
           values (%s,'discovered',%s),(%s,'discovered',%s),
                  (%s,'listing_updated',%s)""",
        (baseline_id, baseline_at, launched_id, now - timedelta(days=5),
         launched_id, now - timedelta(days=1)),
    )
    before_id = db.execute(
        """insert into discovery_listing_snapshots
             (discovered_app_id,captured_at,content_hash,listing)
           values (%s,%s,'focused-before',%s) returning id""",
        (launched_id, now - timedelta(days=3), Jsonb({
            "name": "Real Launch", "pricing": ["Free"],
        })),
    ).fetchone()[0]
    after_at = now - timedelta(hours=12)
    after_id = db.execute(
        """insert into discovery_listing_snapshots
             (discovered_app_id,captured_at,content_hash,listing)
           values (%s,%s,'focused-after',%s) returning id""",
        (launched_id, after_at, Jsonb({
            "name": "Real Launch Pro", "pricing": ["Pro $12 / month"],
        })),
    ).fetchone()[0]
    db.execute(
        """insert into discovery_listing_changes
             (discovered_app_id,snapshot_id,changed_at,field,
              before_value,after_value)
           values (%s,%s,%s,'name',%s,%s),
                  (%s,%s,%s,'pricing',%s,%s)""",
        (launched_id, after_id, after_at, Jsonb("Real Launch"),
         Jsonb("Real Launch Pro"), launched_id, after_id, after_at,
         Jsonb(["Free"]), Jsonb(["Pro $12 / month"])),
    )

    launches = discovery_report(db, activity="new", period_days=7, now=now)
    updates = discovery_report(
        db, activity="updated", period_days=7, pricing="paid", now=now,
    )

    assert [row["handle"] for row in launches["rows"]] == ["real-launch"]
    assert updates["total"] == 1
    assert updates["rows"][0]["changed_fields"] == ["name", "pricing"]
    assert updates["rows"][0]["before_id"] == before_id
    assert updates["rows"][0]["after_id"] == after_id
    assert updates["rows"][0]["verified_changed_at"] == after_at


def test_catalog_search_includes_baseline_apps_categories_and_follow_state(db):
    observed_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    sync_discovered_apps(db, [SitemapApp("alpha-books", None)], observed_at)
    sync_discovery_categories(db, [
        CategoryResult("product-content", "Product content", (
            CategoryApp("alpha-books", "Alpha Books", 42, Decimal("4.8"), 3),
        )),
    ], observed_at)
    app_id = db.execute(
        "select id from discovered_apps where handle='alpha-books'"
    ).fetchone()[0]
    db.execute(
        """insert into discovery_watchlist
             (discovered_app_id,active,follow_source,followed_at)
           values (%s,true,'manual',%s)""",
        (app_id, observed_at),
    )
    db.execute(
        """insert into discovery_listing_snapshots
             (discovered_app_id,captured_at,content_hash,listing)
           values (%s,%s,'hash',%s::jsonb)""",
        (app_id, observed_at, '{"developer":{"name":"North Books"}}'),
    )

    result = search_app_catalog(db, search="North")
    assert result["total"] == 1
    assert result["rows"][0] == {
        "handle": "alpha-books", "name": "Alpha Books",
        "developer": "North Books", "reviews": 42,
        "rating": Decimal("4.80"), "best_rank": 3,
        "categories": "Product content", "followed": True,
        "follow_source": "manual",
        "built_for_shopify": False, "bfs_checked_at": observed_at,
    }
    assert search_app_catalog(db, category="product-content")["total"] == 1
    assert search_app_catalog(db, bfs="not_bfs")["total"] == 1
    assert search_app_catalog(db, bfs="bfs")["total"] == 0
    assert search_app_catalog(db)["rows"] == []


def test_discovery_report_filters_bfs_without_collapsing_unknown(db):
    observed_at = datetime(2026, 8, 12, tzinfo=timezone.utc)
    for handle, value, checked_at in (
        ("official-app", True, observed_at),
        ("checked-app", False, observed_at),
        ("unknown-app", None, None),
    ):
        app_id = db.execute(
            """insert into discovered_apps
                 (handle,display_name,first_seen_at,last_seen_at,is_baseline,
                  built_for_shopify,bfs_checked_at)
               values (%s,%s,%s,%s,false,%s,%s) returning id""",
            (handle, handle, observed_at, observed_at, value, checked_at),
        ).fetchone()[0]
        db.execute(
            """insert into discovery_app_events
                 (discovered_app_id,event_type,occurred_at)
               values (%s,'discovered',%s)""",
            (app_id, observed_at),
        )

    assert [row["handle"] for row in discovery_report(db, bfs="bfs")["rows"]] == [
        "official-app"
    ]
    assert [row["handle"] for row in discovery_report(db, bfs="not_bfs")["rows"]] == [
        "checked-app"
    ]
    assert [row["handle"] for row in discovery_report(db, bfs="unknown")["rows"]] == [
        "unknown-app"
    ]
    assert search_app_catalog(db, bfs="unknown")["rows"][0][
        "built_for_shopify"
    ] is None
