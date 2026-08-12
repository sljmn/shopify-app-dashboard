"""Network and filesystem boundary for followed public App Store listings."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app_dashboard.discovery_watchlist import (
    record_scan_failure,
    store_competitor_snapshot,
)
from app_dashboard.listing_intelligence import (
    USER_AGENT,
    listing_hash,
    parse_listing,
)

MAX_MEDIA_BYTES = 10 * 1024 * 1024
ALLOWED_MEDIA_TYPES = frozenset({
    "image/jpeg", "image/png", "image/webp", "image/gif",
})
DIGEST = re.compile(r"^[0-9a-f]{64}$")
MEDIA_HOSTS = frozenset({"cdn.shopify.com", "cdn.shopifycdn.net"})


@dataclass(frozen=True)
class ArchivedMedia:
    digest: str
    object_key: str
    mime_type: str
    byte_size: int
    width: int | None = None
    height: int | None = None


def media_path(root: Path, digest: str) -> Path:
    if not DIGEST.fullmatch(digest):
        raise ValueError("invalid-media-digest")
    return root / digest[:2] / digest


def _response_bytes(response) -> bytes:
    content = response.content
    if len(content) > MAX_MEDIA_BYTES:
        raise ValueError("media-too-large")
    if not content:
        raise ValueError("empty-media")
    return content


def archive_media(http_get, url: str, root: Path) -> ArchivedMedia:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in MEDIA_HOSTS:
        raise ValueError("invalid-media-url")
    response = http_get(
        url, headers={"User-Agent": USER_AGENT}, timeout=20,
        follow_redirects=True,
    )
    response.raise_for_status()
    final_url = str(getattr(response, "url", url))
    final = urlparse(final_url)
    if final.scheme != "https" or final.hostname not in MEDIA_HOSTS:
        raise ValueError("insecure-media-redirect")
    mime_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if mime_type not in ALLOWED_MEDIA_TYPES:
        raise ValueError("invalid-media-type")
    content = _response_bytes(response)
    digest = hashlib.sha256(content).hexdigest()
    target = media_path(root, digest)
    object_key = f"{digest[:2]}/{digest}"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{digest}.", suffix=".part", dir=target.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return ArchivedMedia(digest, object_key, mime_type, len(content))


def _get_listing(http_get, handle: str) -> dict:
    response = http_get(
        f"https://apps.shopify.com/{handle}",
        headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True,
    )
    response.raise_for_status()
    listing = parse_listing(response.text)
    if not listing["name"]:
        raise ValueError("listing-json-ld-missing")
    return listing


def sync_followed_listing(
    conn, discovered_app_id: int, handle: str, *, media_root: Path,
    http_get=httpx.get, now=None,
) -> dict:
    captured_at = now or datetime.now(timezone.utc)
    try:
        listing = _get_listing(http_get, handle)
        existing = conn.execute(
            """select id from discovery_listing_snapshots
               where discovered_app_id=%s and content_hash=%s""",
            (discovered_app_id, listing_hash(listing)),
        ).fetchone()
        if existing:
            result = store_competitor_snapshot(
                conn, discovered_app_id, listing, (), captured_at
            )
        else:
            sources = []
            if listing.get("icon"):
                sources.append(("icon", 0, listing["icon"]))
            sources.extend(
                ("screenshot", index, url)
                for index, url in enumerate(listing.get("screenshots") or [])
            )
            media = [
                (role, position, url, archive_media(http_get, url, media_root))
                for role, position, url in sources
            ]
            result = store_competitor_snapshot(
                conn, discovered_app_id, listing, media, captured_at
            )
        return {
            "handle": handle, "ok": True, "created": result.created,
            "changes": len(result.changed_fields),
        }
    except (httpx.HTTPError, OSError, ValueError) as exc:
        code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        if code not in {
            "listing-json-ld-missing", "invalid-media-url",
            "insecure-media-redirect", "invalid-media-type", "media-too-large",
            "empty-media", "invalid-media-digest",
        }:
            code = type(exc).__name__
        record_scan_failure(conn, discovered_app_id, captured_at, code)
        return {"handle": handle, "ok": False, "error": code}
