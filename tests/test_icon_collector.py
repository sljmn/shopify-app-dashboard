from datetime import datetime, timezone

from app_dashboard.icon_collector import icon_sync_targets, sync_app_icon


class Response:
    status_code = 200
    headers = {"content-type": "image/png"}
    content = b"public-app-icon"
    url = "https://cdn.shopify.com/app/icon.png"

    def raise_for_status(self):
        pass


def test_icon_backfill_archives_and_links_current_icon(db, tmp_path):
    now = datetime(2026, 8, 12, 18, tzinfo=timezone.utc)
    app_id = db.execute(
        """insert into discovered_apps
             (handle,first_seen_at,last_seen_at,is_baseline,icon_url)
           values ('alpha',%s,%s,false,%s) returning id""",
        (now, now, "https://cdn.shopify.com/app/icon.png"),
    ).fetchone()[0]
    assert icon_sync_targets(db) == [
        (app_id, "alpha", "https://cdn.shopify.com/app/icon.png"),
    ]

    result = sync_app_icon(
        db, app_id, "alpha", "https://cdn.shopify.com/app/icon.png",
        media_root=tmp_path, http_get=lambda *_args, **_kwargs: Response(), now=now,
    )

    assert result["ok"] is True
    digest, archived_url, checked_at = db.execute(
        """select icon_digest,icon_archived_url,icon_checked_at
           from discovered_apps where id=%s""", (app_id,),
    ).fetchone()
    assert archived_url == "https://cdn.shopify.com/app/icon.png"
    assert checked_at == now
    assert (tmp_path / digest[:2] / digest).read_bytes() == b"public-app-icon"
    assert icon_sync_targets(db) == []
