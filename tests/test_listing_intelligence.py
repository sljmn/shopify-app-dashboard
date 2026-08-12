from datetime import datetime, timezone
from pathlib import Path

from app_dashboard.listing_intelligence import (
    parse_autocomplete,
    parse_listing,
    store_listing_snapshot,
    sync_popular_keywords,
)

FIXTURE = Path(__file__).parent / "fixtures/shopify_listing.html"
NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


def test_listing_parser_extracts_stable_fields():
    listing = parse_listing(FIXTURE.read_text())
    assert listing["name"] == "VAT / TAX Exemption"
    assert listing["description"].startswith("Validate EU VAT")
    assert listing["screenshots"] == ["https://cdn.shopify.com/example-1.png"]
    assert listing["rating_count"] == 40


def test_listing_parser_extracts_competitor_strategy_fields():
    html = """
    <script type="application/ld+json">{
      "@type":"SoftwareApplication","name":"Alpha",
      "description":"Recover abandoned carts.",
      "image":"https://cdn.shopify.com/icon.png",
      "aggregateRating":{"ratingValue":"4.8","ratingCount":"120"}
    }</script>
    <p data-app-listing-subtitle>Recover more abandoned carts</p>
    <a href="https://apps.shopify.com/partners/alpha">Alpha Labs</a>
    <span data-language="English"></span><span data-language="Dutch"></span>
    <span data-integration="Klaviyo"></span>
    <div id="app-details"><li>Feature one</li></div>
    <div class="app-details-pricing-plan-card">Starter $9.00/month</div>
    <div data-screenshot-index="0"><img src="https://cdn.shopify.com/screen.png"></div>
    <video><source src="https://cdn.shopify.com/video.mp4"></video>
    """
    listing = parse_listing(html)
    assert listing["subtitle"] == "Recover more abandoned carts"
    assert listing["developer"] == {
        "name": "Alpha Labs", "url": "https://apps.shopify.com/partners/alpha",
    }
    assert listing["languages"] == ["English", "Dutch"]
    assert listing["integrations"] == ["Klaviyo"]
    assert listing["videos"] == ["https://cdn.shopify.com/video.mp4"]
    assert listing["built_for_shopify"] is False


def test_listing_parser_detects_shopifys_official_bfs_badge():
    listing = parse_listing("""
      <script type="application/ld+json">{
        "@type":"SoftwareApplication","name":"Alpha","description":"Useful"
      }</script>
      <div class="built-for-shopify-badge"><span>Built for Shopify</span></div>
    """)
    assert listing["built_for_shopify"] is True


def test_listing_parser_supports_shopifys_label_value_metadata_grid():
    listing = parse_listing("""
      <script type="application/ld+json">{"@type":"SoftwareApplication",
        "name":"Alpha","description":"Useful"}</script>
      <div><dt>Pricing</dt><dd><div>Free plan available. Free trial available.</div></dd></div>
      <div><p>Languages</p><div><p>English, Dutch, and German</p></div></div>
      <div><p>Works with</p><ul><li>Checkout</li><li>Shopify Flow</li></ul></div>
    """)
    assert listing["pricing"] == ["Free plan available. Free trial available."]
    assert listing["languages"] == ["English", "Dutch", "German"]
    assert listing["integrations"] == ["Checkout", "Shopify Flow"]


def test_identical_snapshot_is_reused_and_changes_are_field_level(db, test_app):
    first = store_listing_snapshot(db, test_app.id, "en", {"name": "Old"}, NOW)
    same = store_listing_snapshot(db, test_app.id, "en", {"name": "Old"}, NOW)
    changed = store_listing_snapshot(db, test_app.id, "en", {"name": "New"}, NOW)
    assert same.snapshot_id == first.snapshot_id and not same.created
    assert changed.changed_fields == ("name",)
    assert db.execute(
        "select field,before_value,after_value from aso_listing_changes"
    ).fetchone() == ("name", "Old", "New")


def test_autocomplete_reads_only_search_phrases():
    payload = {
        "searches": [{"name": "Email popup"}],
        "apps": [{"name": "Not a keyword"}],
    }
    assert parse_autocomplete(payload) == ["email popup"]


class Response:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"searches": [{"name": "VAT exemption"}]}


def test_popular_keyword_sync_is_idempotent(db):
    get = lambda *args, **kwargs: Response()
    assert sync_popular_keywords(db, ["vat"], get, NOW, sleep=lambda _: None) == 1
    assert sync_popular_keywords(db, ["vat"], get, NOW, sleep=lambda _: None) == 1
    assert db.execute("select count(*) from aso_popular_keywords").fetchone()[0] == 1
