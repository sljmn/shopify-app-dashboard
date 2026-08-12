"""Public Shopify listing snapshots and autocomplete research."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

from app_dashboard.catalog import AppConfig

LISTING_FIELDS = (
    "name", "subtitle", "description", "features", "pricing", "developer",
    "languages", "integrations", "icon", "screenshots", "videos", "rating",
    "rating_count",
)
AUTOCOMPLETE_URL = "https://apps.shopify.com/search/autocomplete"
USER_AGENT = "Mantle ASO Intelligence/1.0"


@dataclass(frozen=True)
class SnapshotResult:
    snapshot_id: int
    created: bool
    changed_fields: tuple[str, ...]


def _text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _stable_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https":
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _label_value(soup: BeautifulSoup, label: str):
    node = next(
        (text.parent for text in soup.find_all(string=True)
         if _text(text).casefold() == label.casefold()),
        None,
    )
    if not node or not node.parent:
        return None
    values = [child for child in node.parent.find_all(recursive=False) if child is not node]
    return values[0] if values else None


def _comma_values(value: str) -> list[str]:
    value = re.sub(r",?\s+and\s+", ", ", _text(value), flags=re.IGNORECASE)
    return list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


def parse_listing(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    application = {}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or "")
        except (TypeError, json.JSONDecodeError):
            continue
        candidates = value if isinstance(value, list) else [value]
        application = next(
            (item for item in candidates if isinstance(item, dict)
             and item.get("@type") == "SoftwareApplication"),
            application,
        )
    images = application.get("image") or []
    if isinstance(images, str):
        images = [images]
    rating = application.get("aggregateRating") or {}
    screenshots = []
    for image in soup.select('[data-screenshot-index] img[src]'):
        url = _stable_url(image.get("src", ""))
        if url and url not in screenshots:
            screenshots.append(url)
    details = soup.select_one("#app-details")
    features = []
    if details:
        for item in details.select("li"):
            value = _text(item.get_text(" ", strip=True))
            if value and value not in features:
                features.append(value)
    pricing = []
    for card in soup.select(".app-details-pricing-plan-card"):
        value = _text(card.get_text(" ", strip=True))
        if value and value not in pricing:
            pricing.append(value)
    if not pricing:
        pricing_node = _label_value(soup, "Pricing")
        values = [
            _text(child.get_text(" ", strip=True))
            for child in pricing_node.find_all(recursive=False)
        ] if pricing_node else []
        value = next((item for item in values if item), "")
        if not value and pricing_node:
            value = _text(pricing_node.get_text(" ", strip=True))
        if value:
            pricing.append(value)
    subtitle_node = soup.select_one(
        '[data-app-listing-subtitle], .app-listing__subtitle, [data-testid="app-subtitle"]'
    )
    developer_node = soup.select_one(
        'a[href*="/partners/"], a[data-testid="developer-link"]'
    )
    developer = {
        "name": _text(developer_node.get_text(" ", strip=True)) if developer_node else "",
        "url": _stable_url(developer_node.get("href", "")) if developer_node else "",
    }
    languages = []
    for item in soup.select('[data-language], [data-testid="language"]'):
        value = _text(item.get("data-language") or item.get_text(" ", strip=True))
        if value and value not in languages:
            languages.append(value)
    if not languages:
        languages_node = _label_value(soup, "Languages")
        if languages_node:
            languages = _comma_values(languages_node.get_text(" ", strip=True))
    integrations = []
    for item in soup.select('[data-integration], [data-testid="integration"]'):
        value = _text(item.get("data-integration") or item.get_text(" ", strip=True))
        if value and value not in integrations:
            integrations.append(value)
    if not integrations:
        integrations_node = _label_value(soup, "Works with")
        if integrations_node:
            integrations = list(dict.fromkeys(
                _text(item.get_text(" ", strip=True))
                for item in integrations_node.find_all("li")
                if _text(item.get_text(" ", strip=True))
            ))
    videos = []
    for item in soup.select("video[src], video source[src], iframe[src]"):
        value = _stable_url(item.get("src", ""))
        if value and value not in videos:
            videos.append(value)
    return {
        "name": _text(application.get("name")),
        "subtitle": _text(
            subtitle_node.get_text(" ", strip=True) if subtitle_node else ""
        ),
        "description": _text(application.get("description")),
        "features": features,
        "pricing": pricing,
        "developer": developer,
        "languages": languages,
        "integrations": integrations,
        "icon": _stable_url(images[0]) if images else "",
        "screenshots": screenshots,
        "videos": videos,
        "rating": rating.get("ratingValue"),
        "rating_count": rating.get("ratingCount"),
    }


def listing_hash(listing: dict) -> str:
    canonical = {field: listing.get(field) for field in LISTING_FIELDS}
    return hashlib.sha256(
        json.dumps(
            canonical, sort_keys=True, ensure_ascii=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def store_listing_snapshot(
    conn, app_id: int, locale: str, listing: dict, captured_at
) -> SnapshotResult:
    content_hash = listing_hash(listing)
    existing = conn.execute(
        """select id from aso_listing_snapshots
           where app_id=%s and locale=%s and content_hash=%s""",
        (app_id, locale, content_hash),
    ).fetchone()
    if existing:
        return SnapshotResult(existing[0], False, ())
    previous = conn.execute(
        """select listing from aso_listing_snapshots
           where app_id=%s and locale=%s order by captured_at desc, id desc limit 1""",
        (app_id, locale),
    ).fetchone()
    with conn.transaction():
        snapshot_id = conn.execute(
            """insert into aso_listing_snapshots
               (app_id, locale, captured_at, content_hash, listing)
               values (%s,%s,%s,%s,%s) returning id""",
            (app_id, locale, captured_at, content_hash, Jsonb(listing)),
        ).fetchone()[0]
        changed = []
        if previous:
            before = previous[0]
            for field in LISTING_FIELDS:
                if before.get(field) == listing.get(field):
                    continue
                changed.append(field)
                conn.execute(
                    """insert into aso_listing_changes
                       (app_id,snapshot_id,locale,changed_at,field,before_value,after_value)
                       values (%s,%s,%s,%s,%s,%s,%s)""",
                    (app_id, snapshot_id, locale, captured_at, field,
                     Jsonb(before.get(field)), Jsonb(listing.get(field))),
                )
    return SnapshotResult(snapshot_id, True, tuple(changed))


def _source_status(conn, app_id, source, status, fields=None, error=None):
    conn.execute(
        """insert into aso_source_capabilities
           (app_id,source,status,fields,checked_at,error_code)
           values (%s,%s,%s,%s,now(),%s)
           on conflict (app_id,source) do update set
             status=excluded.status, fields=excluded.fields,
             checked_at=excluded.checked_at, error_code=excluded.error_code""",
        (app_id, source, status, Jsonb(fields or {}), error),
    )


def _request_with_retries(http_get, url, *, params=None, sleep=time.sleep):
    response = None
    for attempt in range(3):
        response = http_get(
            url, params=params, headers={"User-Agent": USER_AGENT}, timeout=15
        )
        if response.status_code not in {429, 502, 503, 504}:
            response.raise_for_status()
            return response
        if attempt < 2:
            sleep(2**attempt)
    response.raise_for_status()


def sync_listing(
    conn, app: AppConfig, http_get=httpx.get, now=None, sleep=time.sleep
) -> dict:
    if not app.listing_url:
        return {"status": "unsupported", "snapshots": 0, "changes": 0}
    captured_at = now or datetime.now(timezone.utc)
    snapshots = changes = 0
    try:
        for locale in app.listing_locales:
            response = _request_with_retries(
                http_get, app.listing_url, params={"locale": locale}, sleep=sleep
            )
            listing = parse_listing(response.text)
            if not listing["name"]:
                raise ValueError("listing-json-ld-missing")
            result = store_listing_snapshot(
                conn, app.id, locale, listing, captured_at
            )
            snapshots += int(result.created)
            changes += len(result.changed_fields)
    except (httpx.HTTPError, ValueError) as exc:
        _source_status(conn, app.id, "aso_listing", "failed", error=type(exc).__name__)
        return {"status": "failed", "snapshots": snapshots, "changes": changes}
    _source_status(
        conn, app.id, "aso_listing", "ready",
        {"locales": list(app.listing_locales)},
    )
    return {"status": "ready", "snapshots": snapshots, "changes": changes}


def parse_autocomplete(payload: dict) -> list[str]:
    return sorted({
        _text(item.get("name")).casefold()
        for item in payload.get("searches", [])
        if _text(item.get("name"))
    })


def sync_popular_keywords(
    conn, seeds: Sequence[str], http_get=httpx.get, now=None, sleep=time.sleep
) -> int:
    seen = set()
    timestamp = now or datetime.now(timezone.utc)
    for seed in list(dict.fromkeys(_text(seed).casefold() for seed in seeds if _text(seed)))[:100]:
        response = _request_with_retries(
            http_get, AUTOCOMPLETE_URL, params={"q": seed}, sleep=sleep
        )
        for keyword in parse_autocomplete(response.json()):
            seen.add(keyword)
        sleep(0.1)
    for keyword in seen:
        conn.execute(
            """insert into aso_popular_keywords
               (keyword,source,first_seen_at,last_seen_at)
               values (%s,'autocomplete',%s,%s)
               on conflict (keyword) do update set last_seen_at=excluded.last_seen_at""",
            (keyword, timestamp, timestamp),
        )
    conn.execute(
        """insert into operations_state(source,last_run_at)
           values ('aso_popular_keywords',%s)
           on conflict (source) do update set last_run_at=excluded.last_run_at""",
        (timestamp,),
    )
    return len(seen)


def research_seeds(conn) -> list[str]:
    values = []
    rows = conn.execute(
        """select listing->>'name', listing->>'description'
           from aso_listing_snapshots s
           where id in (select max(id) from aso_listing_snapshots group by app_id,locale)
           union all select keyword, null from aso_keyword_daily"""
    ).fetchall()
    for first, second in rows:
        for text in (first, second):
            words = re.findall(r"[a-z0-9]+", (text or "").casefold())
            values.extend(words)
            values.extend(" ".join(words[index:index + 2]) for index in range(len(words) - 1))
    return list(dict.fromkeys(values))[:100]
