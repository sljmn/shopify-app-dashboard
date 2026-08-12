"""Bounded, incremental collection of public Shopify App Store reviews."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx
from bs4 import BeautifulSoup

from app_dashboard.app_store_discovery import request_with_retries

REVIEW_DATE = "%B %d, %Y"
REPLY_DATE = re.compile(
    r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})\s*$"
)
STARS = re.compile(r"([1-5])\s+out of 5 stars", re.IGNORECASE)
USAGE = re.compile(r"\busing the app\b", re.IGNORECASE)


@dataclass(frozen=True)
class PublicReview:
    shopify_review_id: int
    rating: int
    reviewed_on: date
    merchant_name: str | None
    country: str | None
    usage_duration: str | None
    body: str
    developer_reply: str | None
    developer_replied_on: date | None
    source_url: str


@dataclass(frozen=True)
class ReviewPage:
    reviews: tuple[PublicReview, ...]
    has_next: bool


def _date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), REVIEW_DATE).replace(tzinfo=UTC).date()
    except ValueError:
        return None


def _copy(node) -> str:
    copy = node.select_one("[data-truncate-content-copy]") if node else None
    return copy.get_text(" ", strip=True) if copy else ""


def parse_review_page(html: str, _handle: str) -> ReviewPage:
    soup = BeautifulSoup(html, "html.parser")
    reviews = []
    for node in soup.select("[data-merchant-review][data-review-content-id]"):
        raw_id = (node.get("data-review-content-id") or "").strip()
        if not raw_id.isdigit():
            continue
        rating_node = node.select_one('[role="img"][aria-label*="out of 5 stars"]')
        stars = STARS.search(rating_node.get("aria-label", "") if rating_node else "")
        if not stars:
            continue
        rating_area = rating_node.parent
        date_node = rating_area.select_one(".tw-text-body-xs.tw-text-fg-tertiary")
        reviewed_on = _date(date_node.get_text(" ", strip=True) if date_node else "")
        review_blocks = node.select("[data-truncate-review]")
        body = _copy(review_blocks[0]) if review_blocks else ""
        if reviewed_on is None or not body:
            continue

        merchant_area = node.select_one(".tw-order-1.lg\\:tw-row-span-2")
        merchant_node = merchant_area.select_one("span[title]") if merchant_area else None
        metadata = [
            item.get_text(" ", strip=True)
            for item in merchant_area.find_all("div", recursive=False)
            if item.select_one("span[title]") is None
        ] if merchant_area else []
        usage_duration = next((item for item in metadata if USAGE.search(item)), None)
        country = next(
            (item for item in metadata if item and not USAGE.search(item)), None
        )

        reply_root = node.select_one('[id^="review-reply-"]')
        reply_block = reply_root.select_one("[data-truncate-review]") if reply_root else None
        developer_reply = _copy(reply_block) or None
        developer_replied_on = None
        if reply_root:
            reply_meta = reply_root.select_one(".tw-text-body-xs.tw-text-fg-tertiary")
            match = REPLY_DATE.search(
                reply_meta.get_text(" ", strip=True) if reply_meta else ""
            )
            developer_replied_on = _date(match.group(1)) if match else None

        review_id = int(raw_id)
        reviews.append(PublicReview(
            shopify_review_id=review_id,
            rating=int(stars.group(1)),
            reviewed_on=reviewed_on,
            merchant_name=(
                (merchant_node.get("title") or "").strip() or None
                if merchant_node else None
            ),
            country=country,
            usage_duration=usage_duration,
            body=body,
            developer_reply=developer_reply,
            developer_replied_on=developer_replied_on,
            source_url=f"https://apps.shopify.com/reviews/{review_id}",
        ))
    return ReviewPage(tuple(reviews), soup.select_one('a[rel="next"]') is not None)


def review_sync_targets(conn, *, limit: int = 250) -> list[tuple[int, str]]:
    """Return a fair, bounded queue covering every active discovered app."""
    limit = max(1, min(limit, 2_000))
    return conn.execute(
        """select app.id,app.handle
           from discovered_apps app
           left join discovery_watchlist watch
             on watch.discovered_app_id=app.id
           left join discovery_review_sync_state state
             on state.discovered_app_id=app.id
           left join lateral (
             select latest.review_count-previous.review_count delta
             from discovery_app_observations latest
             left join lateral (
               select review_count from discovery_app_observations older
               where older.discovered_app_id=app.id
                 and older.observed_on < latest.observed_on
               order by older.observed_on desc limit 1
             ) previous on true
             where latest.discovered_app_id=app.id
             order by latest.observed_on desc limit 1
           ) growth on true
           where app.delisted_at is null
           order by
             state.last_attempt_at asc nulls first,
             case
               when watch.active and watch.follow_source='manual' then 0
               when coalesce(growth.delta,0)>0 then 1
               else 2
             end,
             app.id
           limit %s""",
        (limit,),
    ).fetchall()


def _review_url(handle: str, page: int) -> str:
    return (
        f"https://apps.shopify.com/{handle}/reviews"
        f"?sort_by=newest&page={page}"
    )


def _fetch_page(http_get, handle: str, page: int, sleep) -> ReviewPage:
    response = request_with_retries(
        http_get, _review_url(handle, page), sleep=sleep,
    )
    return parse_review_page(response.text, handle)


def _store_reviews(conn, discovered_app_id: int, reviews, captured_at) -> None:
    for review in reviews:
        conn.execute(
            """insert into discovery_reviews
                 (discovered_app_id,shopify_review_id,rating,reviewed_on,
                  merchant_name,country,usage_duration,body,developer_reply,
                  developer_replied_on,source_url,first_captured_at,last_captured_at)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (discovered_app_id,shopify_review_id) do update set
                 rating=excluded.rating,reviewed_on=excluded.reviewed_on,
                 merchant_name=excluded.merchant_name,country=excluded.country,
                 usage_duration=excluded.usage_duration,body=excluded.body,
                 developer_reply=excluded.developer_reply,
                 developer_replied_on=excluded.developer_replied_on,
                 source_url=excluded.source_url,
                 last_captured_at=excluded.last_captured_at""",
            (
                discovered_app_id, review.shopify_review_id, review.rating,
                review.reviewed_on, review.merchant_name, review.country,
                review.usage_duration, review.body, review.developer_reply,
                review.developer_replied_on, review.source_url, captured_at,
                captured_at,
            ),
        )


def _record_success(
    conn, discovered_app_id: int, next_page: int, completed_at, captured_at,
) -> None:
    conn.execute(
        """insert into discovery_review_sync_state
             (discovered_app_id,next_backfill_page,backfill_completed_at,
              last_attempt_at,last_success_at,last_error_code)
           values (%s,%s,%s,%s,%s,null)
           on conflict (discovered_app_id) do update set
             next_backfill_page=excluded.next_backfill_page,
             backfill_completed_at=excluded.backfill_completed_at,
             last_attempt_at=excluded.last_attempt_at,
             last_success_at=excluded.last_success_at,
             last_error_code=null""",
        (discovered_app_id, next_page, completed_at, captured_at, captured_at),
    )


def _record_failure(conn, discovered_app_id: int, attempted_at, code: str) -> None:
    conn.execute(
        """insert into discovery_review_sync_state
             (discovered_app_id,last_attempt_at,last_error_code)
           values (%s,%s,%s)
           on conflict (discovered_app_id) do update set
             last_attempt_at=excluded.last_attempt_at,
             last_error_code=excluded.last_error_code""",
        (discovered_app_id, attempted_at, code),
    )


def sync_app_reviews(
    conn, discovered_app_id: int, handle: str, *, http_get=httpx.get,
    now=None, max_backfill_pages: int = 5, max_incremental_pages: int = 5,
    sleep=time.sleep,
) -> dict:
    """Capture newest reviews and advance a bounded historical backfill."""
    captured_at = now or datetime.now(UTC)
    known_ids = {
        row[0] for row in conn.execute(
            """select shopify_review_id from discovery_reviews
               where discovered_app_id=%s""",
            (discovered_app_id,),
        ).fetchall()
    }
    state = conn.execute(
        """select next_backfill_page,backfill_completed_at
           from discovery_review_sync_state where discovered_app_id=%s""",
        (discovered_app_id,),
    ).fetchone()
    frontier = state[0] if state else 1
    completed_at = state[1] if state else None
    new_ids: set[int] = set()

    def store(page: ReviewPage) -> set[int]:
        ids = {review.shopify_review_id for review in page.reviews}
        new_ids.update(ids - known_ids)
        _store_reviews(conn, discovered_app_id, page.reviews, captured_at)
        return ids

    try:
        newest = _fetch_page(http_get, handle, 1, sleep)
        newest_ids = store(newest)

        if completed_at is not None:
            current = newest
            page_number = 1
            while (
                current.has_next
                and newest_ids - known_ids
                and page_number < max_incremental_pages
            ):
                page_number += 1
                current = _fetch_page(http_get, handle, page_number, sleep)
                page_ids = store(current)
                if not page_ids - known_ids:
                    break
            _record_success(
                conn, discovered_app_id, frontier, completed_at, captured_at,
            )
        else:
            pages_used = 0
            current_page = frontier
            current = newest if frontier == 1 else None
            while pages_used < max_backfill_pages:
                if current is None:
                    current = _fetch_page(
                        http_get, handle, current_page, sleep,
                    )
                    store(current)
                pages_used += 1
                frontier = current_page + 1
                if not current.reviews or not current.has_next:
                    completed_at = captured_at
                    break
                current_page += 1
                current = None
            _record_success(
                conn, discovered_app_id, frontier, completed_at, captured_at,
            )
    except httpx.HTTPStatusError as exc:
        code = f"HTTP_{exc.response.status_code}"
        _record_failure(conn, discovered_app_id, captured_at, code)
        return {"handle": handle, "ok": False, "error": code}
    except (httpx.HTTPError, ValueError) as exc:
        code = type(exc).__name__
        _record_failure(conn, discovered_app_id, captured_at, code)
        return {"handle": handle, "ok": False, "error": code}

    return {
        "handle": handle,
        "ok": True,
        "captured": len(new_ids),
        "backfill_complete": completed_at is not None,
        "next_backfill_page": frontier,
    }


def review_report(
    conn, discovered_app_id: int, *, rating: int | None = None,
    page: int = 1, per_page: int = 30,
) -> dict:
    if rating not in {None, 1, 2, 3, 4, 5}:
        rating = None
    page = max(1, page)
    summary = conn.execute(
        """select count(*),
                  count(*) filter (where developer_reply is not null),
                  count(*) filter (where rating=1),
                  count(*) filter (where rating=2),
                  count(*) filter (where rating=3),
                  count(*) filter (where rating=4),
                  count(*) filter (where rating=5)
           from discovery_reviews where discovered_app_id=%s""",
        (discovered_app_id,),
    ).fetchone()
    filtered_total = conn.execute(
        """select count(*) from discovery_reviews
           where discovered_app_id=%s and (%s::integer is null or rating=%s)""",
        (discovered_app_id, rating, rating),
    ).fetchone()[0]
    pages = max(1, (filtered_total + per_page - 1) // per_page)
    page = min(page, pages)
    rows = conn.execute(
        """select shopify_review_id,rating,reviewed_on,merchant_name,country,
                  usage_duration,body,developer_reply,developer_replied_on,source_url
           from discovery_reviews
           where discovered_app_id=%s and (%s::integer is null or rating=%s)
           order by reviewed_on desc,shopify_review_id desc
           limit %s offset %s""",
        (discovered_app_id, rating, rating, per_page, (page - 1) * per_page),
    ).fetchall()
    state = conn.execute(
        """select next_backfill_page,backfill_completed_at,last_attempt_at,
                  last_success_at,last_error_code
           from discovery_review_sync_state where discovered_app_id=%s""",
        (discovered_app_id,),
    ).fetchone()
    keys = (
        "id", "rating", "reviewed_on", "merchant_name", "country",
        "usage_duration", "body", "developer_reply", "developer_replied_on",
        "source_url",
    )
    return {
        "captured": summary[0],
        "developer_replies": summary[1],
        "distribution": dict(zip(range(1, 6), summary[2:], strict=True)),
        "rows": [dict(zip(keys, row, strict=True)) for row in rows],
        "rating": rating,
        "filtered_total": filtered_total,
        "page": page,
        "pages": pages,
        "state": {
            "next_backfill_page": state[0] if state else 1,
            "backfill_completed_at": state[1] if state else None,
            "last_attempt_at": state[2] if state else None,
            "last_success_at": state[3] if state else None,
            "last_error_code": state[4] if state else None,
        },
    }
