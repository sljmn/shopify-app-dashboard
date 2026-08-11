from datetime import date
from types import SimpleNamespace

from google.api_core.exceptions import ServiceUnavailable

from app_dashboard.aso_ga4 import (
    attribution_key,
    discover_capabilities,
    fetch_keyword_rows,
    normalize_install_source,
    sync_aso_keywords,
    sync_capabilities,
    sync_install_sources,
)


class FakeMetadataClient:
    def __init__(self, dimensions):
        self._dimensions = dimensions

    def get_metadata(self, name):
        assert name == "properties/123/metadata"
        return SimpleNamespace(
            dimensions=[SimpleNamespace(api_name=name) for name in self._dimensions]
        )


def test_discovery_keeps_keyword_and_attribution_status_separate():
    report = discover_capabilities(FakeMetadataClient({
        "searchTerm", "customEvent:position", "customEvent:shop_url",
        "country", "language", "deviceCategory",
    }), "123")
    assert report.statuses == {
        "aso_keywords": "ready", "aso_attribution": "partial",
    }
    assert report.fields["keyword"] == "searchTerm"


def test_discovery_uses_shopify_listing_url_for_keywords_and_positions():
    report = discover_capabilities(FakeMetadataClient({
        "pageLocation", "country", "language", "deviceCategory",
    }), "123")

    assert report.statuses["aso_keywords"] == "ready"
    assert report.fields["page_location"] == "pageLocation"


def test_discovery_marks_attribution_unsupported_without_shop_domain():
    report = discover_capabilities(FakeMetadataClient({"searchTerm"}), "123")
    assert report.statuses["aso_keywords"] == "partial"
    assert report.statuses["aso_attribution"] == "unsupported"
    assert "shop_domain" in report.missing["aso_attribution"]


def test_sync_capabilities_persists_two_source_rows(db, test_app):
    app = test_app.__class__(
        **{**test_app.__dict__, "ga4_property_id": "123"}
    )
    sync_capabilities(db, FakeMetadataClient({"searchTerm"}), app)
    assert db.execute(
        "select source, status from aso_source_capabilities order by source"
    ).fetchall() == [
        ("aso_attribution", "unsupported"), ("aso_keywords", "partial"),
    ]


def _value(value):
    return SimpleNamespace(value=str(value))


def _row(dimensions, metric):
    return SimpleNamespace(
        dimension_values=[_value(value) for value in dimensions],
        metric_values=[_value(metric)],
    )


class KeywordClient:
    def __init__(self):
        self.calls = 0

    def run_report(self, request):
        self.calls += 1
        metric = request.metrics[0].name
        row = _row(["20260810", "vat exemption", "4", "en", "NL", "desktop", "search"], 12 if metric == "totalUsers" else 3)
        return SimpleNamespace(rows=[row], row_count=1)


def test_keyword_fetch_merges_traffic_and_clicks():
    fields = {
        "keyword": "searchTerm", "position": "customEvent:position",
        "locale": "language", "country": "country", "device": "deviceCategory",
        "search_type": "customEvent:search_type",
    }
    rows = fetch_keyword_rows(
        KeywordClient(), "123", fields, date(2026, 8, 10), date(2026, 8, 10)
    )
    assert rows[0]["users"] == 12
    assert rows[0]["install_clicks"] == 3
    assert rows[0]["average_position"] == 4


class ShopifyListingUrlClient:
    def run_report(self, request):
        metric = request.metrics[0].name
        row = _row([
            "20260810",
            "https://apps.shopify.com/tax-exemption?locale=de&surface_detail=vat+id&surface_inter_position=1&surface_intra_position=5&surface_type=search",
            "en-us", "DE", "mobile",
        ], 7 if metric == "totalUsers" else 2)
        return SimpleNamespace(rows=[row], row_count=1)


def test_keyword_fetch_parses_shopify_search_parameters_from_page_location():
    rows = fetch_keyword_rows(
        ShopifyListingUrlClient(),
        "123",
        {
            "page_location": "pageLocation", "locale": "language",
            "country": "country", "device": "deviceCategory",
        },
        date(2026, 8, 10),
        date(2026, 8, 10),
    )

    assert rows == [{
        "date": date(2026, 8, 10), "keyword": "vat id", "locale": "de",
        "country": "DE", "device": "mobile", "search_type": "search",
        "users": 7, "install_clicks": 2, "average_position": 5,
        "latest_position": 5, "position_samples": 7,
    }]


class NonSearchListingUrlClient:
    def run_report(self, request):
        row = _row([
            "20260810",
            "https://apps.shopify.com/tax-exemption?surface_detail=taxes&surface_type=category",
        ], 4)
        return SimpleNamespace(rows=[row], row_count=1)


def test_keyword_fetch_excludes_non_search_shopify_surfaces():
    assert fetch_keyword_rows(
        NonSearchListingUrlClient(), "123", {"page_location": "pageLocation"},
        date(2026, 8, 10), date(2026, 8, 10),
    ) == []


class EmptyKeywordClient:
    def run_report(self, request):
        metric = request.metrics[0].name
        row = _row(
            ["20260810", "   ", "Organic Search"],
            12 if metric == "totalUsers" else 3,
        )
        return SimpleNamespace(rows=[row], row_count=1)


def test_keyword_fetch_discards_rows_without_a_search_term():
    rows = fetch_keyword_rows(
        EmptyKeywordClient(),
        "123",
        {"keyword": "searchTerm", "search_type": "sessionDefaultChannelGroup"},
        date(2026, 8, 10),
        date(2026, 8, 10),
    )

    assert rows == []


def test_empty_keyword_import_marks_the_source_unavailable(db, test_app):
    app = test_app.__class__(
        **{**test_app.__dict__, "ga4_property_id": "123"}
    )
    sync_capabilities(db, FakeMetadataClient({"searchTerm"}), app)

    assert sync_aso_keywords(
        db,
        EmptyKeywordClient(),
        app,
        fields={"keyword": "searchTerm", "search_type": "sessionDefaultChannelGroup"},
        today=date(2026, 8, 10),
        earliest=date(2026, 8, 10),
    ) == 0
    assert db.execute(
        """select status, error_code from aso_source_capabilities
           where app_id=%s and source='aso_keywords'""",
        (app.id,),
    ).fetchone() == ("unsupported", "NoKeywordValues")


class RetryClient(KeywordClient):
    def run_report(self, request):
        self.calls += 1
        if self.calls <= 2:
            raise ServiceUnavailable("later")
        metric = request.metrics[0].name
        row = _row(["20260810", "tax"], 1 if metric == "totalUsers" else 0)
        return SimpleNamespace(rows=[row], row_count=1)


def test_keyword_fetch_retries_transient_errors():
    client = RetryClient()
    fetch_keyword_rows(
        client, "123", {"keyword": "searchTerm"},
        date(2026, 8, 10), date(2026, 8, 10), sleep=lambda _: None,
    )
    assert client.calls == 4


def test_shop_domain_normalization_and_key_are_stable():
    row = normalize_install_source({
        "shop": " HTTPS://Example.MyShopify.com/path ",
        "installed_on": "2026-08-11", "source": "Search",
    })
    assert row["shop_domain"] == "example.myshopify.com"
    assert attribution_key(row) == attribution_key(row)


class AttributionClient:
    def run_report(self, request):
        row = _row(
            ["20260811", "Example.MyShopify.com", "shop-1", "Search", "organic", "vat", "en", "NL", "mobile"],
            1,
        )
        return SimpleNamespace(rows=[row], row_count=1)


def test_install_attribution_is_idempotent(db, test_app):
    fields = {
        "shop_domain": "customEvent:shop_url", "shop_id": "customEvent:shop_id",
        "source": "sessionSource", "source_type": "sessionMedium",
        "keyword": "searchTerm", "locale": "language", "country": "country",
        "device": "deviceCategory",
    }
    assert sync_install_sources(
        db, AttributionClient(), test_app, fields=fields,
        today=date(2026, 8, 11), earliest=date(2026, 8, 1),
    ) == 1
    assert sync_install_sources(
        db, AttributionClient(), test_app, fields=fields,
        today=date(2026, 8, 11), earliest=date(2026, 8, 1),
    ) == 1
    assert db.execute("select count(*) from aso_install_sources").fetchone()[0] == 1
