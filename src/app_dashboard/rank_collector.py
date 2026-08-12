"""Collect and persist organic Shopify App Store keyword positions."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

SEARCH_URL = "https://apps.shopify.com/search"
USER_AGENT = "Mantle Rank Tracker/1.0"
DISPLAY_TZ = ZoneInfo("Europe/Amsterdam")
HANDLE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REVIEW_COUNT = re.compile(
    r"([0-9][0-9.,]*)\s+(?:total reviews?|recensies in totaal)", re.IGNORECASE
)
RATING = re.compile(
    r"([0-5](?:[.,]\d+)?)\s+(?:out of 5 stars|van 5 sterren)", re.IGNORECASE
)


@dataclass(frozen=True)
class RankResult:
    handle: str
    name: str
    position: int
    review_count: int | None = None
    rating: Decimal | None = None
    built_for_shopify: bool = False


def _number(value: str) -> int:
    return int(value.replace(".", "").replace(",", ""))


def parse_search_page(html: str) -> tuple[RankResult, ...]:
    """Parse only organic App Store cards, excluding stories and promos."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, RankResult] = {}
    for card in soup.select('[data-controller="app-card"]'):
        handle = (card.get("data-app-card-handle-value") or "").strip().casefold()
        link = card.select_one('a[href*="surface_type=search"]')
        parsed = urlsplit(link.get("href", "")) if link else None
        query = parse_qs(parsed.query) if parsed else {}
        if not handle and parsed and "/stories/" not in parsed.path:
            handle = parsed.path.strip("/").split("/")[-1].casefold()
        if not HANDLE.fullmatch(handle) or handle in found:
            continue
        try:
            position = int(
                card.get("data-app-card-intra-position-value")
                or query["surface_intra_position"][0]
            )
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if not 1 <= position <= 100:
            continue
        text = card.get_text(" ", strip=True)
        reviews_match = REVIEW_COUNT.search(text)
        rating_match = RATING.search(text)
        try:
            rating = (
                Decimal(rating_match.group(1).replace(",", "."))
                if rating_match
                else None
            )
        except InvalidOperation:
            rating = None
        name = (
            card.get("data-app-card-name-value")
            or (link.get_text(" ", strip=True) if link else "")
            or handle
        ).strip()
        found[handle] = RankResult(
            handle=handle,
            name=name,
            position=position,
            review_count=_number(reviews_match.group(1)) if reviews_match else None,
            rating=rating,
            built_for_shopify="Built for Shopify" in text,
        )
    return tuple(sorted(found.values(), key=lambda row: row.position))


def _request_search(http_get, url: str, sleep, attempts: int = 3):
    error = None
    for attempt in range(attempts):
        try:
            response = http_get(
                url,
                headers={"User-Agent": USER_AGENT, "Turbo-Frame": "search_page"},
                timeout=30,
                follow_redirects=True,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            error = exc
            if attempt + 1 < attempts:
                sleep(2**attempt)
    raise error  # type: ignore[misc]


def collect_keyword_results(keyword, locale, http_get=httpx.get, sleep=time.sleep):
    rows: list[RankResult] = []
    seen: set[str] = set()
    for page in range(1, 6):
        query = str(httpx.QueryParams({"q": keyword, "locale": locale, "page": page}))
        response = _request_search(http_get, f"{SEARCH_URL}?{query}", sleep)
        parsed = parse_search_page(response.text)
        if page == 1 and not parsed:
            raise ValueError("empty-search-results")
        for row in parsed:
            if row.handle in seen:
                continue
            seen.add(row.handle)
            rows.append(
                RankResult(
                    row.handle,
                    row.name,
                    len(rows) + 1,
                    row.review_count,
                    row.rating,
                    row.built_for_shopify,
                )
            )
            if len(rows) == 100:
                return tuple(rows)
        if len(parsed) < 24:
            break
        sleep(0.2)
    return tuple(rows)


def sync_keyword_rankings(
    conn, keyword_id, http_get=httpx.get, sleep=time.sleep, now=None
):
    at = now or datetime.now(UTC)
    captured_on = at.astimezone(DISPLAY_TZ).date()
    row = conn.execute(
        "select keyword, locale from aso_rank_keywords where id=%s and active",
        (keyword_id,),
    ).fetchone()
    if not row:
        raise LookupError("unknown-rank-keyword")
    conn.execute(
        "update aso_rank_keywords set last_scan_attempt_at=%s where id=%s",
        (at, keyword_id),
    )
    try:
        results = collect_keyword_results(row[0], row[1], http_get, sleep)
    except (httpx.HTTPError, ValueError) as exc:
        conn.execute(
            "update aso_rank_keywords set last_scan_error=%s where id=%s",
            (str(exc)[:200], keyword_id),
        )
        return {"status": "failed", "error": type(exc).__name__}
    with conn.transaction():
        conn.execute(
            "delete from aso_rank_scans where rank_keyword_id=%s and captured_on=%s",
            (keyword_id, captured_on),
        )
        scan_id = conn.execute(
            """insert into aso_rank_scans
                   (rank_keyword_id, captured_at, captured_on, result_count)
               values (%s, %s, %s, %s) returning id""",
            (keyword_id, at, captured_on, len(results)),
        ).fetchone()[0]
        for result in results:
            app_id = conn.execute(
                """insert into discovered_apps
                       (handle, display_name, first_seen_at, last_seen_at, is_baseline)
                   values (%s, %s, %s, %s, true)
                   on conflict (handle) do update set
                       display_name=excluded.display_name,
                       last_seen_at=excluded.last_seen_at
                   returning id""",
                (result.handle, result.name, at, at),
            ).fetchone()[0]
            conn.execute(
                """insert into aso_rank_results
                       (rank_scan_id, discovered_app_id, position, display_name,
                        review_count, rating, built_for_shopify)
                   values (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    scan_id,
                    app_id,
                    result.position,
                    result.name,
                    result.review_count,
                    result.rating,
                    result.built_for_shopify,
                ),
            )
        conn.execute(
            """update aso_rank_keywords set
                   last_scanned_at=%s, last_scan_error=null, updated_at=%s
               where id=%s""",
            (at, at, keyword_id),
        )
    return {"status": "ready", "results": len(results)}
