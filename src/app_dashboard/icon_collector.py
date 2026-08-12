"""Bounded archival backfill for public Shopify App Store icons."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx

from app_dashboard.watchlist_collector import archive_media


def icon_sync_targets(conn, *, limit: int = 500) -> list[tuple[int, str, str]]:
    return conn.execute(
        """select id,handle,icon_url from discovered_apps
           where delisted_at is null and icon_url is not null
             and (icon_digest is null or icon_archived_url is distinct from icon_url)
           order by icon_checked_at nulls first,first_seen_at desc,id
           limit %s""",
        (limit,),
    ).fetchall()


def sync_app_icon(
    conn, discovered_app_id: int, handle: str, icon_url: str, *,
    media_root: Path, http_get=httpx.get, now=None,
) -> dict:
    checked_at = now or datetime.now(timezone.utc)
    try:
        archived = archive_media(http_get, icon_url, media_root)
        with conn.transaction():
            conn.execute(
                """insert into discovery_media_objects
                   (digest,object_key,mime_type,byte_size,width,height,created_at)
                   values (%s,%s,%s,%s,%s,%s,%s)
                   on conflict (digest) do nothing""",
                (archived.digest, archived.object_key, archived.mime_type,
                 archived.byte_size, archived.width, archived.height, checked_at),
            )
            conn.execute(
                """update discovered_apps set icon_digest=%s,
                     icon_archived_url=%s,icon_checked_at=%s,icon_error_code=null
                   where id=%s""",
                (archived.digest, icon_url, checked_at, discovered_app_id),
            )
        return {"handle": handle, "ok": True, "digest": archived.digest}
    except (httpx.HTTPError, OSError, ValueError) as exc:
        code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        conn.execute(
            """update discovered_apps set icon_checked_at=%s,icon_error_code=%s
               where id=%s""",
            (checked_at, code, discovered_app_id),
        )
        return {"handle": handle, "ok": False, "error": code}
