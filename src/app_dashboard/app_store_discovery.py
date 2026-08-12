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
        apps.append(CategoryApp(handle, display_name, review_count, rating))
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
                    len(found) + 1,
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
    baseline = conn.execute(
        "select baseline_completed_at from discovery_state where source='apps'"
    ).fetchone() is None
    handles = [app.handle for app in apps]
    existing = {
        row[0] for row in conn.execute(
            "select handle from discovered_apps where handle = any(%s)", (handles,)
        ).fetchall()
    }
    with conn.transaction():
        conn.cursor().executemany(
            """insert into discovered_apps
               (handle,listing_updated_on,first_seen_at,last_seen_at,is_baseline)
               values (%s,%s,%s,%s,%s)
               on conflict (handle) do update set
                 listing_updated_on=excluded.listing_updated_on,
                 last_seen_at=excluded.last_seen_at,
                 is_baseline=discovered_apps.is_baseline or excluded.is_baseline""",
            [
                (app.handle, app.updated_on, observed_at, observed_at, baseline)
                for app in apps
            ],
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
        "seen": len(apps),
        "new": 0 if baseline else len(set(handles) - existing),
        "baseline": baseline,
    }


def sync_discovery_categories(conn, categories: list[CategoryResult], now=None) -> dict:
    if not categories:
        raise ValueError("empty-category-crawl")
    observed_at = now or datetime.now(timezone.utc)
    observed_on = observed_at.astimezone(DISPLAY_TZ).date()
    baseline_pending = conn.execute(
        "select 1 from discovery_state where source='apps'"
    ).fetchone() is None
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
                   (handle,display_name,first_seen_at,last_seen_at,is_baseline)
                   values (%s,%s,%s,%s,%s) on conflict (handle) do update set
                   display_name=coalesce(
                     excluded.display_name,discovered_apps.display_name
                   ),
                   last_seen_at=greatest(discovered_apps.last_seen_at,excluded.last_seen_at)""",
                [(app.handle, app.name, observed_at, observed_at, baseline_pending)
                 for app in category.apps],
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
    }


def growth_signals(conn, *, now=None, limit: int = 20) -> dict:
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
           group by app.id,latest.discovered_app_id,latest.observed_on,
                    latest.review_count,latest.rating,latest.best_category_rank,
                    previous.observed_on,previous.review_count,
                    day7.observed_on,day7.review_count,
                    day30.observed_on,day30.review_count"""
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
        def delta(value):
            return None if reviews is None or value is None else reviews - value
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


def discovery_report(
    conn, *, search: str = "", category: str = "", page: int = 1,
    per_page: int = 100, now=None,
) -> dict:
    current = now or datetime.now(timezone.utc)
    local_now = current.astimezone(DISPLAY_TZ)
    this_week = (local_now - timedelta(days=local_now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    chart_start = this_week - timedelta(weeks=11)
    params: list = []
    where = ["not app.is_baseline"]
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
    filtered = " and ".join(where)
    total_filtered = conn.execute(
        f"select count(*) from discovered_apps app where {filtered}", params
    ).fetchone()[0]
    page = max(1, min(page, max(1, (total_filtered + per_page - 1) // per_page)))
    rows = conn.execute(
        f"""select app.handle,app.display_name,app.first_seen_at,
                   app.listing_updated_on,
                   coalesce(string_agg(distinct category.name, ', '
                     order by category.name), '') categories
            from discovered_apps app
            left join discovered_app_categories member
              on member.discovered_app_id=app.id
            left join discovery_categories category on category.id=member.category_id
            where {filtered}
            group by app.id order by app.first_seen_at desc,app.handle
            limit %s offset %s""",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()
    weekly_rows = conn.execute(
        f"""select (date_trunc('week', first_seen_at at time zone
                       'Europe/Amsterdam'))::date week, count(*)
           from discovered_apps app where {filtered} and first_seen_at >= %s
           group by week order by week""",
        [*params, chart_start.astimezone(timezone.utc)],
    ).fetchall()
    weekly = dict(weekly_rows)
    weeks = [
        {"start": (chart_start + timedelta(weeks=index)).date(),
         "count": weekly.get((chart_start + timedelta(weeks=index)).date(), 0)}
        for index in range(12)
    ]
    state = conn.execute(
        "select last_success_at from discovery_state where source='apps'"
    ).fetchone()
    return {
        "indexed": conn.execute(
            "select count(*) from discovered_apps"
        ).fetchone()[0],
        "new_this_week": conn.execute(
            """select count(*) from discovered_apps
               where not is_baseline and first_seen_at >= %s""",
            (this_week.astimezone(timezone.utc),),
        ).fetchone()[0],
        "new_last_7_days": conn.execute(
            """select count(*) from discovered_apps
               where not is_baseline and first_seen_at >= %s""",
            (current - timedelta(days=7),),
        ).fetchone()[0],
        "last_scan": state[0] if state else None,
        "weeks": weeks,
        "max_week": max((item["count"] for item in weeks), default=0),
        "categories": conn.execute(
            "select slug,name,app_count from discovery_categories order by name"
        ).fetchall(),
        "rows": rows,
        "total": total_filtered,
        "page": page,
        "pages": max(1, (total_filtered + per_page - 1) // per_page),
    }
