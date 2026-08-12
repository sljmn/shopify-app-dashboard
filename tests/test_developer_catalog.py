from datetime import datetime, timezone
from pathlib import Path

from app_dashboard.developer_catalog import (
    developer_detail,
    normalize_developer_url,
    parse_developer_page,
    sync_developer_catalog,
    upsert_developer_from_listing,
)

FIXTURE = Path(__file__).parent / "fixtures/shopify_developer.html"
NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)


def app(db, handle="contentpilot"):
    return db.execute(
        """insert into discovered_apps
             (handle,display_name,first_seen_at,last_seen_at,is_baseline)
           values (%s,'ContentPilot',now(),now(),false) returning id""",
        (handle,),
    ).fetchone()[0]


def test_developer_url_is_stable_and_shopify_only():
    assert normalize_developer_url(
        "https://apps.shopify.com/partners/pilot/?locale=en#apps"
    ) == "https://apps.shopify.com/partners/pilot"
    assert normalize_developer_url("https://example.com/partners/pilot") is None
    assert normalize_developer_url("https://apps.shopify.com/contentpilot") is None


def test_developer_page_parser_extracts_unique_listing_handles():
    apps = parse_developer_page(FIXTURE.read_text())
    assert [(item.handle, item.name) for item in apps] == [
        ("contentpilot", "ContentPilot"),
        ("image-translate-easy", "Image Translate Easy"),
    ]


class Response:
    text = FIXTURE.read_text()

    def raise_for_status(self):
        pass


def test_developer_catalog_upserts_all_apps_without_counting_them_as_new(db):
    app_id = app(db)
    developer_id = upsert_developer_from_listing(
        db, app_id, {"developer": {
            "name": "Pilot Labs",
            "url": "https://apps.shopify.com/partners/pilot?locale=en",
        }}, now=NOW,
    )
    result = sync_developer_catalog(
        db, developer_id, http_get=lambda *args, **kwargs: Response(), now=NOW,
    )
    assert result == {"developer_id": developer_id, "apps": 2, "status": "ready"}
    detail = developer_detail(db, developer_id)
    assert [item["handle"] for item in detail["apps"]] == [
        "contentpilot", "image-translate-easy",
    ]
    assert db.execute(
        "select is_baseline from discovered_apps where handle='image-translate-easy'"
    ).fetchone()[0] is True
