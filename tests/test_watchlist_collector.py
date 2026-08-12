from datetime import datetime, timedelta, timezone

import pytest

from app_dashboard.app_store_discovery import SitemapApp, sync_discovered_apps
from app_dashboard.discovery_watchlist import follow_app
from app_dashboard.watchlist_collector import (
    archive_media,
    media_path,
    sync_followed_listing,
)

NOW = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)


class Response:
    def __init__(self, *, text="", content=b"", mime="text/html", url="https://example.test"):
        self.text = text
        self.content = content
        self.headers = {"content-type": mime}
        self.url = url
        self.status_code = 200

    def raise_for_status(self):
        pass


def listing(name="Alpha", price="$9", screenshot="one.png"):
    return f"""
      <script type="application/ld+json">{{"@type":"SoftwareApplication",
      "name":"{name}","description":"Useful app","image":"https://cdn.shopify.com/icon.png"}}</script>
      <div class="app-details-pricing-plan-card">{price}</div>
      <div data-screenshot-index="0"><img src="https://cdn.shopify.com/{screenshot}"></div>
    """


def fake_get(html):
    def get(url, **kwargs):
        if url.startswith("https://apps.shopify.com/"):
            return Response(text=html, url=url)
        return Response(content=("bytes:" + url).encode(), mime="image/png", url=url)
    return get


def test_media_is_content_addressed_and_deduplicated(tmp_path):
    get = fake_get(listing())
    first = archive_media(get, "https://cdn.shopify.com/icon.png", tmp_path)
    second = archive_media(get, "https://cdn.shopify.com/icon.png", tmp_path)
    assert first == second
    assert media_path(tmp_path, first.digest).read_bytes() == b"bytes:https://cdn.shopify.com/icon.png"
    assert len(list(tmp_path.rglob(first.digest))) == 1


def test_media_archive_refuses_non_shopify_hosts(tmp_path):
    with pytest.raises(ValueError, match="invalid-media-url"):
        archive_media(fake_get(listing()), "https://127.0.0.1/private", tmp_path)


def test_followed_listing_versions_only_changed_content(db, tmp_path):
    sync_discovered_apps(db, [SitemapApp("alpha", None)], NOW)
    follow_app(db, "alpha", source="manual", now=NOW)
    app_id = db.execute("select id from discovered_apps where handle='alpha'").fetchone()[0]
    first = sync_followed_listing(
        db, app_id, "alpha", media_root=tmp_path,
        http_get=fake_get(listing()), now=NOW,
    )
    same = sync_followed_listing(
        db, app_id, "alpha", media_root=tmp_path,
        http_get=fake_get(listing()), now=NOW + timedelta(days=1),
    )
    changed = sync_followed_listing(
        db, app_id, "alpha", media_root=tmp_path,
        http_get=fake_get(listing(price="$19", screenshot="two.png")),
        now=NOW + timedelta(days=2),
    )
    assert first["created"] is True
    assert same["created"] is False
    assert changed == {"handle": "alpha", "ok": True, "created": True, "changes": 2}
    assert db.execute("select count(*) from discovery_listing_snapshots").fetchone()[0] == 2
    assert db.execute(
        "select field from discovery_listing_changes order by field"
    ).fetchall() == [("pricing",), ("screenshots",)]


def test_media_failure_does_not_publish_partial_snapshot(db, tmp_path):
    sync_discovered_apps(db, [SitemapApp("alpha", None)], NOW)
    follow_app(db, "alpha", source="manual", now=NOW)
    app_id = db.execute("select id from discovered_apps where handle='alpha'").fetchone()[0]

    def get(url, **kwargs):
        if url.startswith("https://apps.shopify.com/"):
            return Response(text=listing(), url=url)
        return Response(content=b"not image", mime="text/plain", url=url)

    result = sync_followed_listing(
        db, app_id, "alpha", media_root=tmp_path, http_get=get, now=NOW,
    )
    assert result == {"handle": "alpha", "ok": False, "error": "invalid-media-type"}
    assert db.execute("select count(*) from discovery_listing_snapshots").fetchone()[0] == 0
