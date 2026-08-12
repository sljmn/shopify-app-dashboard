"""Store-wide Shopify App Store discovery from public sitemap/category pages."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

APP_SITEMAP_URL = "https://apps.shopify.com/sitemap_apps_en.xml"
CATEGORY_SITEMAP_URL = "https://apps.shopify.com/sitemap_categories_en.xml"
CATEGORY_URL = (
    "https://apps.shopify.com/categories/{slug}/all"
    "?page={page}&surface_type=category"
)
USER_AGENT = "Mantle App Discovery/1.0"
HANDLE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
NON_APPS = frozenset({
    "en", "nl", "de", "fr", "es", "it", "ja", "ko", "cs", "da", "fi",
    "nb", "pl", "pt", "sv", "th", "tr", "vi", "zh", "categories",
    "collections", "partners", "stories", "blog", "built-in-features",
    "app-groups",
})
DISPLAY_TZ = ZoneInfo("Europe/Amsterdam")


@dataclass(frozen=True)
class SitemapApp:
    handle: str
    updated_on: date | None


@dataclass(frozen=True)
class CategoryApp:
    handle: str
    name: str | None
    review_count: int | None = None
    rating: Decimal | None = None
    rank: int | None = None
    built_for_shopify: bool = False


@dataclass(frozen=True)
class CategoryResult:
    slug: str
    name: str
    apps: tuple[CategoryApp, ...]


def _xml_urls(xml: str):
    root = ElementTree.fromstring(xml)
    for node in root.findall("{*}url"):
        location = node.findtext("{*}loc", "").strip()
        modified = node.findtext("{*}lastmod", "").strip()
        yield location, modified


def _shopify_path(value: str) -> tuple[str, ...]:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc != "apps.shopify.com":
        return ()
    return tuple(part for part in parsed.path.split("/") if part)


def parse_app_sitemap(xml: str) -> list[SitemapApp]:
    apps: dict[str, SitemapApp] = {}
    for location, modified in _xml_urls(xml):
        path = _shopify_path(location)
        if len(path) != 1 or path[0] in NON_APPS or not HANDLE.fullmatch(path[0]):
            continue
        try:
            updated_on = date.fromisoformat(modified) if modified else None
        except ValueError:
            updated_on = None
        apps[path[0]] = SitemapApp(path[0], updated_on)
    return list(apps.values())


def parse_category_sitemap(xml: str) -> list[str]:
    slugs = []
    for location, _ in _xml_urls(xml):
        path = _shopify_path(location)
        if (len(path) == 2 and path[0] == "categories"
                and HANDLE.fullmatch(path[1]) and path[1] not in NON_APPS):
            slugs.append(path[1])
    return list(dict.fromkeys(slugs))


def parse_category_page(html: str, slug: str) -> tuple[str, list[CategoryApp]]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    match = re.match(r"Best\s+(.+?)\s+Apps\s+For\b", title, re.IGNORECASE)
    name = match.group(1).strip() if match else slug.replace("-", " ").title()
    apps: list[CategoryApp] = []
    for card in soup.select('[data-controller="app-card"]'):
        handle = (card.get("data-app-card-handle-value") or "").strip()
        if not HANDLE.fullmatch(handle) or handle in NON_APPS:
            continue
        display_name = (card.get("data-app-card-name-value") or "").strip() or None
        card_text = card.get_text(" ", strip=True)
        reviews_match = re.search(r"([0-9,]+)\s+total reviews?", card_text)
        rating_match = re.search(r"([0-5](?:\.\d+)?)\s+out of 5 stars", card_text)
        review_count = (
            int(reviews_match.group(1).replace(",", "")) if reviews_match else None
        )
        rating = Decimal(rating_match.group(1)) if rating_match else None
        apps.append(CategoryApp(
            handle, display_name, review_count, rating, None,
            card.select_one(".built-for-shopify-badge") is not None,
        ))
    return name, list({app.handle: app for app in apps}.values())


def request_with_retries(
    http_get, url: str, *, sleep=time.sleep, allow_not_found: bool = False
):
    response = None
    for attempt in range(3):
        response = http_get(
            url, headers={"User-Agent": USER_AGENT}, timeout=30,
            follow_redirects=True,
        )
        if response.status_code == 404 and allow_not_found:
            return response
        if response.status_code not in {429, 502, 503, 504}:
            response.raise_for_status()
            return response
        if attempt < 2:
            sleep(2**attempt)
    response.raise_for_status()


def collect_apps(http_get=httpx.get, sleep=time.sleep) -> list[SitemapApp]:
    response = request_with_retries(http_get, APP_SITEMAP_URL, sleep=sleep)
    return parse_app_sitemap(response.text)


def collect_categories(
    http_get=httpx.get,
    sleep=time.sleep,
    *,
    max_pages: int = 40,
    page_delay: float = 0.25,
) -> list[CategoryResult]:
    response = request_with_retries(http_get, CATEGORY_SITEMAP_URL, sleep=sleep)
    results = []
    for slug in parse_category_sitemap(response.text):
        found: dict[str, CategoryApp] = {}
        name = slug.replace("-", " ").title()
        for page in range(1, max_pages + 1):
            response = request_with_retries(
                http_get, CATEGORY_URL.format(slug=slug, page=page), sleep=sleep,
                allow_not_found=True,
            )
            if response.status_code == 404:
                break
            parsed_name, apps = parse_category_page(response.text, slug)
            name = parsed_name or name
            fresh = [app for app in apps if app.handle not in found]
            if not fresh:
                break
            for app in fresh:
                found[app.handle] = CategoryApp(
                    app.handle, app.name, app.review_count, app.rating,
                    len(found) + 1, app.built_for_shopify,
                )
            sleep(page_delay)
        # Umbrella categories have no populated /all page and add no useful tag.
        if found:
            results.append(CategoryResult(slug, name, tuple(found.values())))
    return results


def sync_discovered_apps(conn, apps: list[SitemapApp], now=None) -> dict:
    if not apps:
        raise ValueError("empty-app-sitemap")
    observed_at = now or datetime.now(timezone.utc)
    state = conn.execute(
        "select baseline_completed_at from discovery_state where source='apps'"
    ).fetchone()
    baseline = state is None or state[0] is None
    apps_by_handle = {app.handle: app for app in apps}
    handles = list(apps_by_handle)
    existing = {
        row[1]: {
            "id": row[0], "updated_on": row[2], "delisted_at": row[3],
        }
        for row in conn.execute(
            "select id,handle,listing_updated_on,delisted_at from discovered_apps"
        ).fetchall()
    }
    new_handles = set(handles) - set(existing)
    listing_updates = [
        (existing[handle]["id"], existing[handle]["updated_on"], app.updated_on)
        for handle, app in apps_by_handle.items()
        if handle in existing and existing[handle]["updated_on"] is not None
        and app.updated_on is not None
        and existing[handle]["updated_on"] != app.updated_on
    ]
    relisted = [
        existing[handle]["id"] for handle in handles
        if handle in existing and existing[handle]["delisted_at"] is not None
    ]
    with conn.transaction():
        conn.cursor().executemany(
            """insert into discovered_apps
               (handle,listing_updated_on,first_seen_at,last_seen_at,is_baseline,
                missing_scan_count,delisted_at)
               values (%s,%s,%s,%s,%s,0,null)
               on conflict (handle) do update set
                 listing_updated_on=coalesce(
                   excluded.listing_updated_on,discovered_apps.listing_updated_on
                 ),
                 last_seen_at=excluded.last_seen_at,
                 missing_scan_count=0,delisted_at=null""",
            [
                (app.handle, app.updated_on, observed_at, observed_at, baseline)
                for app in apps_by_handle.values()
            ],
        )
        if not baseline and new_handles:
            conn.execute(
                """insert into discovery_app_events
                     (discovered_app_id,event_type,occurred_at,listing_updated_on)
                   select id,'discovered',%s,listing_updated_on
                   from discovered_apps where handle=any(%s)""",
                (observed_at, list(new_handles)),
            )
            conn.execute(
                """insert into discovery_watchlist
                     (discovered_app_id,active,follow_source,followed_at)
                   select id,true,'new_app',%s from discovered_apps
                   where handle=any(%s)
                   on conflict (discovered_app_id) do nothing""",
                (observed_at, list(new_handles)),
            )
        if listing_updates:
            conn.cursor().executemany(
                """insert into discovery_app_events
                     (discovered_app_id,event_type,occurred_at,
                      previous_listing_updated_on,listing_updated_on,details)
                   values (%s,'listing_updated',%s,%s,%s,%s)""",
                [
                    (app_id, observed_at, before, after,
                     Jsonb({"source": "shopify_sitemap"}))
                    for app_id, before, after in listing_updates
                ],
            )
        if relisted:
            conn.cursor().executemany(
                """insert into discovery_app_events
                     (discovered_app_id,event_type,occurred_at,listing_updated_on)
                   select id,'relisted',%s,listing_updated_on
                   from discovered_apps where id=%s""",
                [(observed_at, app_id) for app_id in relisted],
            )
        conn.execute(
            """update discovered_apps set missing_scan_count=missing_scan_count+1
               where not (handle=any(%s))""",
            (handles,),
        )
        delisted = conn.execute(
            """update discovered_apps set delisted_at=%s
               where missing_scan_count >= 3 and delisted_at is null
               returning id,listing_updated_on""",
            (observed_at,),
        ).fetchall()
        if delisted:
            conn.cursor().executemany(
                """insert into discovery_app_events
                     (discovered_app_id,event_type,occurred_at,listing_updated_on)
                   values (%s,'delisted',%s,%s)""",
                [(app_id, observed_at, updated_on) for app_id, updated_on in delisted],
            )
            conn.execute(
                """insert into discovery_alerts
                     (event_key,alert_type,discovered_app_id,created_at,payload)
                   select 'delisted:' || event.id,'delisted',event.discovered_app_id,
                          event.occurred_at,jsonb_build_object('handle',app.handle)
                   from discovery_app_events event
                   join discovered_apps app on app.id=event.discovered_app_id
                   join discovery_watchlist watch
                     on watch.discovered_app_id=event.discovered_app_id
                    and watch.active
                   where event.event_type='delisted' and event.occurred_at=%s
                   on conflict (event_key) do nothing""",
                (observed_at,),
            )
        conn.execute(
            """insert into discovery_state
               (source,baseline_completed_at,last_success_at)
               values ('apps',%s,%s)
               on conflict (source) do update set
                 baseline_completed_at=coalesce(
                   discovery_state.baseline_completed_at,
                   excluded.baseline_completed_at
                 ), last_success_at=excluded.last_success_at""",
            (observed_at if baseline else None, observed_at),
        )
    return {
        "seen": len(apps_by_handle),
        "new": 0 if baseline else len(new_handles),
        "baseline": baseline,
    }


def sync_discovery_categories(conn, categories: list[CategoryResult], now=None) -> dict:
    if not categories:
        raise ValueError("empty-category-crawl")
    observed_at = now or datetime.now(timezone.utc)
    observed_on = observed_at.astimezone(DISPLAY_TZ).date()
    state = conn.execute(
        "select baseline_completed_at from discovery_state where source='apps'"
    ).fetchone()
    baseline_pending = state is None or state[0] is None
    handles = {
        app.handle for category in categories for app in category.apps
    }
    existing_handles = {
        row[0] for row in conn.execute(
            "select handle from discovered_apps where handle=any(%s)",
            (list(handles),),
        ).fetchall()
    } if handles else set()
    new_handles = handles - existing_handles
    with conn.transaction():
        for category in categories:
            conn.execute(
                """insert into discovery_categories (slug,name,app_count,observed_at)
                   values (%s,%s,%s,%s) on conflict (slug) do update set
                   name=excluded.name, app_count=excluded.app_count,
                   observed_at=excluded.observed_at""",
                (category.slug, category.name, len(category.apps), observed_at),
            )
            conn.cursor().executemany(
                """insert into discovered_apps
                   (handle,display_name,first_seen_at,last_seen_at,is_baseline,
                    built_for_shopify,bfs_checked_at)
                   values (%s,%s,%s,%s,%s,%s,%s) on conflict (handle) do update set
                   display_name=coalesce(
                     excluded.display_name,discovered_apps.display_name
                   ),
                   last_seen_at=greatest(discovered_apps.last_seen_at,excluded.last_seen_at),
                   built_for_shopify=excluded.built_for_shopify,
                   bfs_checked_at=excluded.bfs_checked_at""",
                [(app.handle, app.name, observed_at, observed_at, baseline_pending,
                  app.built_for_shopify, observed_at)
                 for app in category.apps],
            )
        if not baseline_pending and new_handles:
            conn.execute(
                """insert into discovery_app_events
                     (discovered_app_id,event_type,occurred_at,listing_updated_on,
                      details)
                   select id,'discovered',%s,listing_updated_on,%s
                   from discovered_apps where handle=any(%s)""",
                (
                    observed_at,
                    Jsonb({"source": "shopify_category_crawl"}),
                    list(new_handles),
                ),
            )
            conn.execute(
                """insert into discovery_watchlist
                     (discovered_app_id,active,follow_source,followed_at)
                   select id,true,'new_app',%s from discovered_apps
                   where handle=any(%s)
                   on conflict (discovered_app_id) do nothing""",
                (observed_at, list(new_handles)),
            )
        for category in categories:
            conn.execute(
                """delete from discovered_app_categories
                   where category_id=(select id from discovery_categories where slug=%s)""",
                (category.slug,),
            )
            conn.cursor().executemany(
                """insert into discovered_app_categories (discovered_app_id,category_id)
                   select app.id, category.id from discovered_apps app
                   cross join discovery_categories category
                   where app.handle=%s and category.slug=%s
                   on conflict do nothing""",
                [(app.handle, category.slug) for app in category.apps],
            )
            conn.cursor().executemany(
                """insert into discovery_category_observations
                   (discovered_app_id,category_id,observed_on,position,observed_at)
                   select app.id,category.id,%s,%s,%s
                   from discovered_apps app cross join discovery_categories category
                   where app.handle=%s and category.slug=%s
                   on conflict (discovered_app_id,category_id,observed_on)
                   do update set position=excluded.position,
                                 observed_at=excluded.observed_at""",
                [
                    (observed_on, app.rank, observed_at, app.handle, category.slug)
                    for app in category.apps if app.rank is not None
                ],
            )
        app_metrics: dict[str, dict] = {}
        for category in categories:
            for app in category.apps:
                metrics = app_metrics.setdefault(app.handle, {
                    "review_count": app.review_count,
                    "rating": app.rating,
                    "best_rank": app.rank,
                })
                if app.review_count is not None and (
                    metrics["review_count"] is None
                    or app.review_count > metrics["review_count"]
                ):
                    metrics["review_count"] = app.review_count
                    metrics["rating"] = app.rating
                if app.rank is not None:
                    metrics["best_rank"] = min(
                        rank for rank in (metrics["best_rank"], app.rank)
                        if rank is not None
                    )
        conn.cursor().executemany(
            """insert into discovery_app_observations
               (discovered_app_id,observed_on,review_count,rating,
                best_category_rank,observed_at)
               select id,%s,%s,%s,%s,%s from discovered_apps where handle=%s
               on conflict (discovered_app_id,observed_on) do update set
                 review_count=excluded.review_count,rating=excluded.rating,
                 best_category_rank=excluded.best_category_rank,
                 observed_at=excluded.observed_at""",
            [
                (observed_on, values["review_count"], values["rating"],
                 values["best_rank"], observed_at, handle)
                for handle, values in app_metrics.items()
                if values["best_rank"] is not None
            ],
        )
        conn.execute(
            """insert into discovery_state (source,last_success_at)
               values ('categories',%s) on conflict (source) do update set
               last_success_at=excluded.last_success_at""",
            (observed_at,),
        )
    return {
        "categories": len(categories),
        "memberships": sum(len(category.apps) for category in categories),
        "new": 0 if baseline_pending else len(new_handles),
    }


def growth_signals(
    conn, *, now=None, limit: int = 20, category: str = "",
) -> dict:
    current = now or datetime.now(timezone.utc)
    today = current.astimezone(DISPLAY_TZ).date()
    observations = conn.execute(
        """with latest as (
               select distinct on (o.discovered_app_id)
                 o.discovered_app_id,o.observed_on,o.review_count,o.rating,
                 o.best_category_rank
               from discovery_app_observations o
               order by o.discovered_app_id,o.observed_on desc
           )
           select app.id,app.handle,app.display_name,app.first_seen_at,
                  app.is_baseline,latest.observed_on,latest.review_count,
                  latest.rating,latest.best_category_rank,
                  previous.observed_on,previous.review_count,
                  day7.observed_on,day7.review_count,
                  day30.observed_on,day30.review_count,
                  coalesce(string_agg(distinct category.name, ', '
                    order by category.name),'') categories
           from latest
           join discovered_apps app on app.id=latest.discovered_app_id
           left join lateral (
             select o.observed_on,o.review_count
             from discovery_app_observations o
             where o.discovered_app_id=app.id and o.observed_on < latest.observed_on
             order by o.observed_on desc limit 1
           ) previous on true
           left join lateral (
             select o.observed_on,o.review_count
             from discovery_app_observations o
             where o.discovered_app_id=app.id
               and o.observed_on <= latest.observed_on - 7
             order by o.observed_on desc limit 1
           ) day7 on true
           left join lateral (
             select o.observed_on,o.review_count
             from discovery_app_observations o
             where o.discovered_app_id=app.id
               and o.observed_on <= latest.observed_on - 30
             order by o.observed_on desc limit 1
           ) day30 on true
           left join discovered_app_categories member
             on member.discovered_app_id=app.id
           left join discovery_categories category on category.id=member.category_id
           where (%s='' or exists (
             select 1 from discovered_app_categories selected_member
             join discovery_categories selected_category
               on selected_category.id=selected_member.category_id
             where selected_member.discovered_app_id=app.id
               and selected_category.slug=%s
           ))
           group by app.id,latest.discovered_app_id,latest.observed_on,
                    latest.review_count,latest.rating,latest.best_category_rank,
                    previous.observed_on,previous.review_count,
                    day7.observed_on,day7.review_count,
                    day30.observed_on,day30.review_count""",
        (category.strip(), category.strip()),
    ).fetchall()
    rank_changes = {
        row[0]: row[2] - row[1]
        for row in conn.execute(
            """with latest_category_rank as (
                   select distinct on (o.discovered_app_id,o.category_id)
                     o.discovered_app_id,o.category_id,o.observed_on,o.position
                   from discovery_category_observations o
                   order by o.discovered_app_id,o.category_id,o.observed_on desc
               ), current_rank as (
                   select distinct on (o.discovered_app_id)
                     o.discovered_app_id,o.category_id,o.observed_on,o.position
                   from latest_category_rank o
                   order by o.discovered_app_id,o.position,o.observed_on desc
               )
               select current_rank.discovered_app_id,current_rank.position,
                      previous.position
               from current_rank
               left join lateral (
                 select o.position from discovery_category_observations o
                 where o.discovered_app_id=current_rank.discovered_app_id
                   and o.category_id=current_rank.category_id
                   and o.observed_on < current_rank.observed_on
                 order by o.observed_on desc limit 1
               ) previous on true
               where previous.position is not null"""
        ).fetchall()
    }
    rows = []
    for row in observations:
        (app_id, handle, name, first_seen, is_baseline, observed_on, reviews,
         rating, best_rank, previous_on, previous_reviews, day7_on, day7_reviews,
         day30_on, day30_reviews, categories) = row
        def delta(value, current=reviews):
            return None if current is None or value is None else current - value
        previous_delta = delta(previous_reviews)
        delta7 = delta(day7_reviews)
        delta30 = delta(day30_reviews)
        comparison_on = day30_on or day7_on or previous_on
        comparison_reviews = (
            day30_reviews if day30_on else day7_reviews if day7_on else previous_reviews
        )
        comparison_delta = delta(comparison_reviews)
        comparison_days = (
            max(1, (observed_on - comparison_on).days) if comparison_on else None
        )
        velocity30 = (
            comparison_delta * 30 / comparison_days
            if comparison_delta is not None and comparison_days else None
        )
        relative_growth = (
            comparison_delta * 100 / comparison_reviews
            if comparison_delta is not None and comparison_reviews else None
        )
        age_days = max(0, (today - first_seen.astimezone(DISPLAY_TZ).date()).days)
        rows.append({
            "handle": handle, "name": name or handle.replace("-", " ").title(),
            "reviews": reviews, "rating": rating, "best_rank": best_rank,
            "rank_change": rank_changes.get(app_id), "previous_delta": previous_delta,
            "delta7": delta7, "delta30": delta30,
            "relative_growth": relative_growth, "velocity30": velocity30,
            "comparison_days": comparison_days, "age_days": age_days,
            "is_baseline": is_baseline, "categories": categories,
        })
    positive = [row for row in rows if (row["velocity30"] or 0) > 0]
    fastest = sorted(
        positive,
        key=lambda row: (row["velocity30"], row["delta30"] or -1,
                         row["reviews"] or 0), reverse=True,
    )[:limit]
    gems = [
        row for row in positive
        if not row["is_baseline"] and row["age_days"] <= 180
        and 5 <= (row["reviews"] or 0) <= 250
        and (row["previous_delta"] or 0) >= 3
    ]
    for row in gems:
        row["score"] = round(
            (row["velocity30"] or 0)
            + min(row["relative_growth"] or 0, 200) * 0.15
            + max(row["rank_change"] or 0, 0) * 2,
            1,
        )
    gems.sort(key=lambda row: (row["score"], row["reviews"] or 0), reverse=True)
    contenders = sorted(
        [row for row in rows if not row["is_baseline"] and row["age_days"] <= 60],
        key=lambda row: (row["velocity30"] or 0, row["reviews"] or 0,
                         -(row["best_rank"] or 99999)), reverse=True,
    )[:limit]
    return {"gems": gems[:limit], "fastest": fastest, "contenders": contenders}


def run_app_discovery(conn, http_get=httpx.get, now=None, sleep=time.sleep) -> dict:
    return sync_discovered_apps(conn, collect_apps(http_get, sleep), now)


def run_category_discovery(conn, http_get=httpx.get, now=None, sleep=time.sleep) -> dict:
    return sync_discovery_categories(
        conn, collect_categories(http_get, sleep), now
    )


def pricing_profile(listing: dict | None) -> dict:
    values = [str(value) for value in (listing or {}).get("pricing") or []]
    text = " ".join(values)
    lowered = text.casefold()
    monthly = sorted({
        Decimal(value.replace(",", ""))
        for value in re.findall(
            r"\$\s*([0-9][0-9,.]*)\s*(?:/|per\s+)\s*(?:month|mo)\b",
            text, re.IGNORECASE,
        )
    })
    annual = sorted({
        Decimal(value.replace(",", ""))
        for value in re.findall(
            r"\$\s*([0-9][0-9,.]*)\s*(?:/|per\s+)\s*(?:year|yr)\b",
            text, re.IGNORECASE,
        )
    })
    has_free = "free plan" in lowered or any(
        value.casefold().startswith("free") for value in values
    )
    has_paid = bool(monthly or annual)
    trial_match = re.search(r"(\d+)\s*[- ]?day[^.]*free trial", text, re.IGNORECASE)
    trial = f"{trial_match.group(1)}-day trial" if trial_match else (
        "Free trial" if "free trial" in lowered else ""
    )
    if has_free and has_paid:
        label = "Free + paid"
    elif has_free:
        label = "Free"
    elif has_paid:
        label = "Paid"
    else:
        label = "Unknown"
    return {
        "label": label, "monthly": monthly, "annual": annual, "trial": trial,
        "summary": values[0] if values else "",
    }


def next_category_scan(now=None) -> datetime:
    local_now = (now or datetime.now(timezone.utc)).astimezone(DISPLAY_TZ)
    for days in range(8):
        candidate = (local_now + timedelta(days=days)).replace(
            hour=4, minute=0, second=0, microsecond=0
        )
        if candidate.weekday() in {1, 4} and candidate > local_now:
            return candidate
    raise RuntimeError("category-scan-calendar")


def category_dashboard(
    conn, slug: str, *, search: str = "", signal: str = "all",
    bfs: str = "", page: int = 1, per_page: int = 100, now=None,
) -> dict | None:
    category = conn.execute(
        """select id,name,observed_at from discovery_categories
           where slug=%s""",
        (slug.strip(),),
    ).fetchone()
    if not category:
        return None
    category_id, category_name, observed_at = category
    current = now or datetime.now(timezone.utc)
    today = current.astimezone(DISPLAY_TZ).date()
    cutoff7 = today - timedelta(days=7)
    cutoff30 = today - timedelta(days=30)
    signal = signal if signal in {
        "all", "new", "reviews", "fastest", "listing", "delisted",
    } else "all"

    where = ["member.category_id=%s"]
    params: list = [cutoff7, cutoff30, category_id]
    if signal == "delisted":
        where.append("app.delisted_at is not null")
    else:
        where.append("app.delisted_at is null")
    if signal == "new":
        where.append(
            "exists (select 1 from discovery_app_events event "
            "where event.discovered_app_id=app.id "
            "and event.event_type='discovered' and event.occurred_at >= %s)"
        )
        params.append(current - timedelta(days=30))
    elif signal in {"reviews", "fastest"}:
        where.append(
            "latest.review_count is not null and previous.review_count is not null "
            "and latest.review_count > previous.review_count"
        )
    elif signal == "listing":
        where.append("verified.changed_at >= %s")
        params.append(current - timedelta(days=30))
    if search.strip():
        term = f"%{search.strip()}%"
        where.append(
            "(app.handle ilike %s or coalesce(app.display_name,'') ilike %s "
            "or coalesce(snapshot.listing->'developer'->>'name','') ilike %s)"
        )
        params.extend([term, term, term])
    if bfs == "bfs":
        where.append("app.built_for_shopify is true")
    elif bfs == "not_bfs":
        where.append("app.built_for_shopify is false")
    elif bfs == "unknown":
        where.append("app.built_for_shopify is null")

    filtered = " and ".join(where)
    base = f"""from discovered_app_categories member
        join discovered_apps app on app.id=member.discovered_app_id
        left join lateral (
          select review_count,rating,best_category_rank,observed_on
          from discovery_app_observations
          where discovered_app_id=app.id order by observed_on desc limit 1
        ) latest on true
        left join lateral (
          select review_count from discovery_app_observations
          where discovered_app_id=app.id and observed_on < latest.observed_on
          order by observed_on desc limit 1
        ) previous on true
        left join lateral (
          select review_count from discovery_app_observations
          where discovered_app_id=app.id and observed_on <= %s
          order by observed_on desc limit 1
        ) prior7 on true
        left join lateral (
          select review_count from discovery_app_observations
          where discovered_app_id=app.id and observed_on <= %s
          order by observed_on desc limit 1
        ) prior30 on true
        left join lateral (
          select position from discovery_category_observations
          where discovered_app_id=app.id and category_id=member.category_id
          order by observed_on desc limit 1
        ) category_rank on true
        left join lateral (
          select listing from discovery_listing_snapshots
          where discovered_app_id=app.id order by captured_at desc,id desc limit 1
        ) snapshot on true
        left join lateral (
          select changed_at from discovery_listing_changes
          where discovered_app_id=app.id order by changed_at desc,id desc limit 1
        ) verified on true
        where {filtered}"""
    total = conn.execute(f"select count(*) {base}", params).fetchone()[0]
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    order = (
        "(latest.review_count-previous.review_count) desc nulls last, "
        if signal == "fastest" else
        "category_rank.position asc nulls last, "
    )
    raw_rows = conn.execute(
        f"""select app.handle,app.display_name,category_rank.position,
                   latest.review_count,latest.rating,
                   case when latest.review_count is not null
                          and previous.review_count is not null
                        then latest.review_count-previous.review_count end
                     latest_delta,
                   case when latest.review_count is not null
                          and prior7.review_count is not null
                        then latest.review_count-prior7.review_count end delta7,
                   case when latest.review_count is not null
                          and prior30.review_count is not null
                        then latest.review_count-prior30.review_count end delta30,
                   app.built_for_shopify,app.bfs_checked_at,snapshot.listing,
                   verified.changed_at,app.first_seen_at,app.delisted_at
            {base}
            order by {order}coalesce(app.display_name,app.handle),app.handle
            limit %s offset %s""",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    keys = (
        "handle", "name", "rank", "reviews", "rating", "latest_delta",
        "delta7", "delta30",
        "built_for_shopify", "bfs_checked_at", "listing", "listing_changed_at",
        "first_seen_at", "delisted_at",
    )
    rows = []
    for raw in raw_rows:
        row = dict(zip(keys, raw, strict=True))
        row["name"] = row["name"] or row["handle"].replace("-", " ").title()
        row["developer"] = (
            ((row["listing"] or {}).get("developer") or {}).get("name") or ""
        )
        row["pricing"] = pricing_profile(row["listing"])
        rows.append(row)

    summary = conn.execute(
        """select
             count(*) filter (where app.delisted_at is null) total_apps,
             count(*) filter (where app.delisted_at is null
                              and latest.review_count is not null) measured_apps,
             count(*) filter (where app.delisted_at is null
                              and app.built_for_shopify is true) bfs_apps,
             count(*) filter (where app.delisted_at is null and exists (
               select 1 from discovery_app_events event
               where event.discovered_app_id=app.id
                 and event.event_type='discovered' and event.occurred_at >= %s
             )) new_30d,
             count(*) filter (where app.delisted_at is null
                              and latest.review_count is not null
                              and previous.review_count is not null
                              and latest.review_count > previous.review_count)
               review_gainers
           from discovered_app_categories member
           join discovered_apps app on app.id=member.discovered_app_id
           left join lateral (
             select review_count,observed_on from discovery_app_observations
             where discovered_app_id=app.id order by observed_on desc limit 1
           ) latest on true
           left join lateral (
             select review_count from discovery_app_observations
             where discovered_app_id=app.id and observed_on < latest.observed_on
             order by observed_on desc limit 1
           ) previous on true
           where member.category_id=%s""",
        (current - timedelta(days=30), category_id),
    ).fetchone()
    return {
        "category_name": category_name, "last_scan": observed_at,
        "total_apps": summary[0], "measured_apps": summary[1],
        "bfs_apps": summary[2], "new_30d": summary[3],
        "review_gainers": summary[4], "rows": rows, "total": total,
        "page": page, "pages": pages, "signal": signal,
    }


def category_opportunities(conn, *, now=None) -> list[dict]:
    current = now or datetime.now(timezone.utc)
    core = conn.execute(
        """with latest as (
             select distinct on (discovered_app_id)
               discovered_app_id,review_count
             from discovery_app_observations
             order by discovered_app_id,observed_on desc
           ), previous as (
             select latest.discovered_app_id,
               (select o.review_count from discovery_app_observations o
                where o.discovered_app_id=latest.discovered_app_id
                  and o.observed_on < (
                    select max(observed_on) from discovery_app_observations recent
                    where recent.discovered_app_id=latest.discovered_app_id
                  )
                order by o.observed_on desc limit 1) review_count
             from latest
           )
           select category.id,category.slug,category.name,count(*) apps,
             count(latest.review_count) reviews_covered,
             count(*) filter (where latest.review_count=0) zero_reviews,
             percentile_cont(0.5) within group (order by latest.review_count)
               filter (where latest.review_count is not null) median_reviews,
             count(*) filter (
               where app.listing_updated_on <= %s
                 and previous.review_count is not null
                 and latest.review_count <= previous.review_count
             ) dormant
           from discovery_categories category
           join discovered_app_categories member on member.category_id=category.id
           join discovered_apps app on app.id=member.discovered_app_id
             and app.delisted_at is null
           left join latest on latest.discovered_app_id=app.id
           left join previous on previous.discovered_app_id=app.id
           group by category.id order by category.name""",
        (current.astimezone(DISPLAY_TZ).date() - timedelta(days=365),),
    ).fetchall()
    pricing_by_category: dict[int, list[dict]] = {}
    for category_id, listing in conn.execute(
        """with latest as (
             select distinct on (discovered_app_id) discovered_app_id,listing
             from discovery_listing_snapshots
             order by discovered_app_id,captured_at desc,id desc
           )
           select member.category_id,latest.listing
           from latest join discovered_app_categories member
             on member.discovered_app_id=latest.discovered_app_id"""
    ).fetchall():
        pricing_by_category.setdefault(category_id, []).append(
            pricing_profile(listing)
        )
    max_apps = max((row[3] for row in core), default=1)
    result = []
    for (category_id, slug, name, app_count, reviews_covered, zero_reviews,
         median_reviews, dormant) in core:
        profiles = pricing_by_category.get(category_id, [])
        known = [profile for profile in profiles if profile["label"] != "Unknown"]
        paid = [profile for profile in known if profile["label"] in {"Paid", "Free + paid"}]
        monthly_prices = []
        for profile in paid:
            if profile["monthly"]:
                monthly_prices.append(profile["monthly"][0])
            elif profile["annual"]:
                monthly_prices.append(profile["annual"][0] / Decimal(12))
        zero_share = (zero_reviews / reviews_covered) if reviews_covered else 0
        observed_median = float(median_reviews or 0)
        score = round(
            (1 - app_count / max_apps) * 45
            + zero_share * 30
            + (1 - min(observed_median / 100, 1)) * 25
        )
        result.append({
            "slug": slug, "name": name, "apps": app_count,
            "reviews_covered": reviews_covered,
            "median_reviews": int(observed_median),
            "zero_review_share": round(zero_share * 100),
            "pricing_covered": len(known),
            "paid_share": round(len(paid) * 100 / len(known)) if known else None,
            "average_monthly_price": (
                sum(monthly_prices, Decimal(0)) / len(monthly_prices)
                if monthly_prices else None
            ),
            "dormant": dormant, "score": max(0, min(score, 100)),
        })
    return sorted(result, key=lambda row: (-row["score"], row["name"]))


def discovery_report(
    conn, *, search: str = "", category: str = "", page: int = 1,
    per_page: int = 100, activity: str = "new", bfs: str = "",
    pricing: str = "", period_days: int | None = None, now=None,
) -> dict:
    current = now or datetime.now(timezone.utc)
    local_now = current.astimezone(DISPLAY_TZ)
    this_week = (local_now - timedelta(days=local_now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    chart_start = this_week - timedelta(weeks=11)
    activity = activity if activity in {"new", "updated", "delisted", "relisted"} else "new"
    event_type = {
        "new": "discovered", "updated": "listing_updated",
        "delisted": "delisted", "relisted": "relisted",
    }[activity]
    params: list = []
    where = ["event.event_type=%s"]
    params.append(event_type)
    if activity == "new":
        where.append("app.is_baseline is false")
    if period_days in {7, 30, 90}:
        where.append("event.occurred_at >= %s")
        params.append(current - timedelta(days=period_days))
    if search.strip():
        where.append(
            "(app.handle ilike %s or coalesce(app.display_name,'') ilike %s)"
        )
        term = f"%{search.strip()}%"
        params.extend([term, term])
    if category.strip():
        where.append(
            "exists (select 1 from discovered_app_categories member "
            "join discovery_categories category on category.id=member.category_id "
            "where member.discovered_app_id=app.id and category.slug=%s)"
        )
        params.append(category.strip())
    if bfs == "bfs":
        where.append("app.built_for_shopify is true")
    elif bfs == "not_bfs":
        where.append("app.built_for_shopify is false")
    elif bfs == "unknown":
        where.append("app.built_for_shopify is null")
    pricing_text = "lower(coalesce(snapshot.listing->'pricing','[]'::jsonb)::text)"
    paid_pricing = f"{pricing_text} ~ '\\$[^]]*(month|mo|year|yr)'"
    if pricing == "free":
        where.append(f"{pricing_text} like '%free%'")
    elif pricing == "paid":
        where.append(paid_pricing)
    elif pricing == "unknown":
        where.extend([
            f"{pricing_text} not like '%free%'", f"not ({paid_pricing})",
        ])
    filtered = " and ".join(where)
    base = f"""from discovery_app_events event
            join discovered_apps app on app.id=event.discovered_app_id
            left join lateral (
              select review_count,rating,best_category_rank
              from discovery_app_observations
              where discovered_app_id=app.id order by observed_on desc limit 1
            ) observation on true
            left join lateral (
              select listing from discovery_listing_snapshots
              where discovered_app_id=app.id order by captured_at desc,id desc limit 1
            ) snapshot on true
            left join lateral (
              select changed_snapshot.captured_at changed_at,
                     changed_snapshot.id after_id,previous_snapshot.id before_id,
                     array_agg(change.field order by change.field) changed_fields
              from discovery_listing_snapshots changed_snapshot
              join discovery_listing_changes change
                on change.snapshot_id=changed_snapshot.id
              left join lateral (
                select id from discovery_listing_snapshots
                where discovered_app_id=app.id
                  and (captured_at,id) <
                      (changed_snapshot.captured_at,changed_snapshot.id)
                order by captured_at desc,id desc limit 1
              ) previous_snapshot on true
              where changed_snapshot.discovered_app_id=app.id
                and changed_snapshot.captured_at >= event.occurred_at
              group by changed_snapshot.id,previous_snapshot.id
              order by changed_snapshot.captured_at,changed_snapshot.id limit 1
            ) verified on true
            left join lateral (
              select coalesce(string_agg(distinct category.name, ', '
                       order by category.name),'') names
              from discovered_app_categories member
              join discovery_categories category on category.id=member.category_id
              where member.discovered_app_id=app.id
            ) category_names on true
            where {filtered}"""
    total_filtered = conn.execute(
        f"select count(*) {base}",
        params,
    ).fetchone()[0]
    page = max(1, min(page, max(1, (total_filtered + per_page - 1) // per_page)))
    raw_rows = conn.execute(
        f"""select app.handle,app.display_name,event.occurred_at,event.event_type,
                   event.previous_listing_updated_on,event.listing_updated_on,
                   app.listing_updated_on,app.delisted_at,
                   observation.review_count,observation.rating,
                   observation.best_category_rank,snapshot.listing,
                   verified.changed_at,verified.changed_fields,
                   verified.before_id,verified.after_id,category_names.names,
                   app.built_for_shopify,app.bfs_checked_at
            {base}
            order by event.occurred_at desc,event.id desc
            limit %s offset %s""",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    developer_counts = {
        name.casefold(): count
        for name, count in conn.execute(
            """with latest as (
                 select distinct on (discovered_app_id)
                   listing->'developer'->>'name' developer
                 from discovery_listing_snapshots
                 order by discovered_app_id,captured_at desc,id desc
               )
               select developer,count(*) from latest
               where coalesce(developer,'')<>'' group by developer"""
        ).fetchall()
    }
    keys = (
        "handle", "name", "event_at", "event_type", "previous_updated_on",
        "event_updated_on", "listing_updated_on", "delisted_at", "reviews",
        "rating", "best_rank", "listing", "verified_changed_at",
        "changed_fields", "before_id", "after_id", "categories",
        "built_for_shopify", "bfs_checked_at",
    )
    rows = []
    for raw in raw_rows:
        row = dict(zip(keys, raw, strict=True))
        row["last_verified_change"] = row["verified_changed_at"]
        row["name"] = row["name"] or row["handle"].replace("-", " ").title()
        developer = ((row["listing"] or {}).get("developer") or {}).get("name") or ""
        row["developer"] = developer
        row["developer_app_count"] = developer_counts.get(developer.casefold(), 0)
        row["pricing"] = pricing_profile(row["listing"])
        rows.append(row)
    weekly_rows = conn.execute(
        """select (date_trunc('week', event.occurred_at at time zone
                     'Europe/Amsterdam'))::date week,event.event_type,count(*)
           from discovery_app_events event
           join discovered_apps app on app.id=event.discovered_app_id
           where event.occurred_at >= %s
             and (event.event_type<>'discovered' or app.is_baseline is false)
           group by week,event.event_type order by week,event.event_type""",
        (chart_start.astimezone(timezone.utc),),
    ).fetchall()
    weekly = {(week, kind): count for week, kind, count in weekly_rows}
    weeks = [
        {"start": (chart_start + timedelta(weeks=index)).date(),
         "new": weekly.get(((chart_start + timedelta(weeks=index)).date(),
                            "discovered"), 0),
         "updated": weekly.get(((chart_start + timedelta(weeks=index)).date(),
                                "listing_updated"), 0),
         "delisted": weekly.get(((chart_start + timedelta(weeks=index)).date(),
                                 "delisted"), 0)}
        for index in range(12)
    ]
    first_activity = next(
        (index for index, week in enumerate(weeks)
         if week["new"] or week["updated"] or week["delisted"]),
        len(weeks) - 1,
    )
    weeks = weeks[first_activity:]
    state = conn.execute(
        "select last_success_at from discovery_state where source='apps'"
    ).fetchone()
    recent_counts = dict(conn.execute(
        """select event.event_type,count(*) from discovery_app_events event
           join discovered_apps app on app.id=event.discovered_app_id
           where event.occurred_at >= %s
             and (event.event_type<>'discovered' or app.is_baseline is false)
           group by event.event_type""",
        (current - timedelta(days=7),),
    ).fetchall())
    return {
        "indexed": conn.execute(
            "select count(*) from discovered_apps"
        ).fetchone()[0],
        "active_indexed": conn.execute(
            "select count(*) from discovered_apps where delisted_at is null"
        ).fetchone()[0],
        "new_this_week": conn.execute(
            """select count(*) from discovery_app_events event
               join discovered_apps app on app.id=event.discovered_app_id
               where event.event_type='discovered'
                 and app.is_baseline is false and event.occurred_at >= %s""",
            (this_week.astimezone(timezone.utc),),
        ).fetchone()[0],
        "new_last_7_days": recent_counts.get("discovered", 0),
        "updated_last_7_days": recent_counts.get("listing_updated", 0),
        "delisted_last_7_days": recent_counts.get("delisted", 0),
        "last_scan": state[0] if state else None,
        "weeks": weeks,
        "max_week": max(
            (max(item["new"], item["updated"], item["delisted"])
             for item in weeks), default=0,
        ),
        "categories": conn.execute(
            "select slug,name,app_count from discovery_categories order by name"
        ).fetchall(),
        "rows": rows,
        "activity": activity,
        "next_category_scan": next_category_scan(current),
        "total": total_filtered,
        "page": page,
        "pages": max(1, (total_filtered + per_page - 1) // per_page),
    }


def search_app_catalog(
    conn, *, search: str = "", category: str = "", page: int = 1,
    per_page: int = 50, bfs: str = "",
) -> dict:
    search = search.strip()
    category = category.strip()
    if not search and not category and not bfs:
        return {"rows": [], "total": 0, "page": 1, "pages": 1}
    where = []
    params = []
    if search:
        term = f"%{search}%"
        where.append(
            """(app.handle ilike %s or coalesce(app.display_name,'') ilike %s
                or coalesce(snapshot.listing->'developer'->>'name','') ilike %s
                or exists (
                    select 1 from discovered_app_categories search_member
                    join discovery_categories search_category
                      on search_category.id=search_member.category_id
                    where search_member.discovered_app_id=app.id
                      and search_category.name ilike %s
                ))"""
        )
        params.extend([term, term, term, term])
    if category:
        where.append(
            """exists (
                select 1 from discovered_app_categories filter_member
                join discovery_categories filter_category
                  on filter_category.id=filter_member.category_id
                where filter_member.discovered_app_id=app.id
                  and filter_category.slug=%s
            )"""
        )
        params.append(category)
    if bfs == "bfs":
        where.append("app.built_for_shopify is true")
    elif bfs == "not_bfs":
        where.append("app.built_for_shopify is false")
    elif bfs == "unknown":
        where.append("app.built_for_shopify is null")
    filtered = " and ".join(where)
    base = f"""from discovered_apps app
        left join lateral (
          select o.review_count,o.rating,o.best_category_rank
          from discovery_app_observations o
          where o.discovered_app_id=app.id
          order by o.observed_on desc limit 1
        ) observation on true
        left join lateral (
          select s.listing from discovery_listing_snapshots s
          where s.discovered_app_id=app.id
          order by s.captured_at desc,s.id desc limit 1
        ) snapshot on true
        left join discovery_watchlist watch on watch.discovered_app_id=app.id
        left join lateral (
          select coalesce(string_agg(distinct category.name, ', '
                   order by category.name),'') names
          from discovered_app_categories member
          join discovery_categories category on category.id=member.category_id
          where member.discovered_app_id=app.id
        ) category_names on true
        where {filtered}"""
    total = conn.execute(f"select count(*) {base}", params).fetchone()[0]
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    rows = conn.execute(
        f"""select app.handle,app.display_name,
                   snapshot.listing->'developer'->>'name' developer,
                   observation.review_count,observation.rating,
                   observation.best_category_rank,
                   category_names.names categories,
                   coalesce(watch.active,false),watch.follow_source,
                   app.built_for_shopify,app.bfs_checked_at
            {base}
            order by coalesce(app.display_name,app.handle),app.handle
            limit %s offset %s""",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    keys = (
        "handle", "name", "developer", "reviews", "rating", "best_rank",
        "categories", "followed", "follow_source",
        "built_for_shopify", "bfs_checked_at",
    )
    return {
        "rows": [dict(zip(keys, row, strict=True)) for row in rows],
        "total": total, "page": page, "pages": pages,
    }
