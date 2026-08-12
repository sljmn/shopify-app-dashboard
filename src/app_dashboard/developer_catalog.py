"""Normalize Shopify developers and maintain their public app catalogs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "Mantle Developer Research/1.0"
SHOPIFY_HOSTS = frozenset({"apps.shopify.com", "apps.shopify.com."})
RESERVED_PATHS = frozenset({
    "about", "categories", "collections", "compare", "login", "partners",
    "privacy", "search", "sitemap", "stories", "terms",
})
HANDLE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class DeveloperApp:
    handle: str
    name: str


def normalize_developer_url(value: str) -> str | None:
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").casefold()
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    if parsed.scheme != "https" or host not in SHOPIFY_HOSTS:
        return None
    if not path.startswith("/partners/") or len(path.split("/")) < 3:
        return None
    return urlunsplit(("https", "apps.shopify.com", path, "", ""))


def _listing_handle(href: str) -> str | None:
    parsed = urlsplit(urljoin("https://apps.shopify.com", href))
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in SHOPIFY_HOSTS:
        return None
    pieces = [piece for piece in parsed.path.split("/") if piece]
    if not pieces:
        return None
    if pieces[0] == "apps" and len(pieces) == 2:
        candidate = pieces[1]
    elif len(pieces) == 1:
        candidate = pieces[0]
    else:
        return None
    candidate = candidate.casefold()
    if candidate in RESERVED_PATHS or not HANDLE.fullmatch(candidate):
        return None
    return candidate


def parse_developer_page(html: str) -> tuple[DeveloperApp, ...]:
    soup = BeautifulSoup(html, "html.parser")
    apps = {}
    for link in soup.select("a[href]"):
        handle = _listing_handle(link.get("href", ""))
        if not handle:
            continue
        name_node = link.select_one("h2, h3, [data-app-name]")
        raw_name = (
            name_node.get_text(" ", strip=True) if name_node
            else link.get("aria-label") or link.get_text(" ", strip=True)
        )
        name = re.sub(r"\s+", " ", raw_name or "").strip() or handle.replace("-", " ")
        apps.setdefault(handle, DeveloperApp(handle, name))
    return tuple(apps.values())


def upsert_developer_from_listing(
    conn, discovered_app_id: int, listing: dict, *, now=None,
) -> int | None:
    developer = listing.get("developer") or {}
    url = normalize_developer_url(developer.get("url", ""))
    name = re.sub(r"\s+", " ", developer.get("name", "")).strip()
    if not url or not name:
        return None
    observed_at = now or datetime.now(timezone.utc)
    developer_id = conn.execute(
        """insert into discovered_developers
             (name,shopify_url,created_at,updated_at)
           values (%s,%s,%s,%s)
           on conflict (shopify_url) do update set
             name=excluded.name,updated_at=excluded.updated_at
           returning id""",
        (name, url, observed_at, observed_at),
    ).fetchone()[0]
    conn.execute(
        """insert into discovered_app_developers
             (discovered_app_id,discovered_developer_id,created_at)
           values (%s,%s,%s) on conflict do nothing""",
        (discovered_app_id, developer_id, observed_at),
    )
    return developer_id


def sync_developer_catalog(conn, developer_id: int, http_get=httpx.get, *, now=None) -> dict:
    attempted_at = now or datetime.now(timezone.utc)
    row = conn.execute(
        "select shopify_url from discovered_developers where id=%s", (developer_id,)
    ).fetchone()
    if not row:
        raise LookupError("unknown-developer")
    try:
        response = http_get(
            row[0], headers={"User-Agent": USER_AGENT}, timeout=20,
            follow_redirects=True,
        )
        response.raise_for_status()
        apps = parse_developer_page(response.text)
        with conn.transaction():
            for app in apps:
                app_id = conn.execute(
                    """insert into discovered_apps
                         (handle,display_name,first_seen_at,last_seen_at,is_baseline)
                       values (%s,%s,%s,%s,true)
                       on conflict (handle) do update set
                         display_name=coalesce(nullif(discovered_apps.display_name,''),
                                               excluded.display_name),
                         last_seen_at=greatest(discovered_apps.last_seen_at,
                                              excluded.last_seen_at)
                       returning id""",
                    (app.handle, app.name, attempted_at, attempted_at),
                ).fetchone()[0]
                conn.execute(
                    """insert into discovered_app_developers
                         (discovered_app_id,discovered_developer_id,created_at)
                       values (%s,%s,%s) on conflict do nothing""",
                    (app_id, developer_id, attempted_at),
                )
            conn.execute(
                """update discovered_developers set last_scan_attempt_at=%s,
                     last_scanned_at=%s,last_scan_error=null,updated_at=%s where id=%s""",
                (attempted_at, attempted_at, attempted_at, developer_id),
            )
        return {"developer_id": developer_id, "apps": len(apps), "status": "ready"}
    except (httpx.HTTPError, ValueError) as exc:
        conn.execute(
            """update discovered_developers set last_scan_attempt_at=%s,
                 last_scan_error=%s,updated_at=%s where id=%s""",
            (attempted_at, type(exc).__name__, attempted_at, developer_id),
        )
        return {"developer_id": developer_id, "apps": 0, "status": "failed",
                "error": type(exc).__name__}


def developer_detail(conn, developer_id: int) -> dict | None:
    row = conn.execute(
        """select id,name,shopify_url,last_scan_attempt_at,last_scanned_at,
                  last_scan_error,created_at,updated_at
           from discovered_developers where id=%s""",
        (developer_id,),
    ).fetchone()
    if not row:
        return None
    keys = (
        "id", "name", "shopify_url", "last_scan_attempt_at", "last_scanned_at",
        "last_scan_error", "created_at", "updated_at",
    )
    result = dict(zip(keys, row, strict=True))
    result["apps"] = [
        {"id": app_id, "handle": handle, "name": name, "followed": followed,
         "reviews": reviews, "rating": rating}
        for app_id, handle, name, followed, reviews, rating in conn.execute(
            """select app.id,app.handle,app.display_name,coalesce(watch.active,false),
                      observation.review_count,observation.rating
               from discovered_app_developers member
               join discovered_apps app on app.id=member.discovered_app_id
               left join discovery_watchlist watch on watch.discovered_app_id=app.id
               left join lateral (
                 select review_count,rating from discovery_app_observations
                 where discovered_app_id=app.id order by observed_on desc limit 1
               ) observation on true
               where member.discovered_developer_id=%s
               order by coalesce(app.display_name,app.handle),app.handle""",
            (developer_id,),
        ).fetchall()
    ]
    return result


def developers_due_for_refresh(conn, *, limit: int = 100) -> list[int]:
    return [
        row[0] for row in conn.execute(
            """select distinct developer.id
               from discovered_developers developer
               join discovered_app_developers member
                 on member.discovered_developer_id=developer.id
               left join research_list_apps list_app
                 on list_app.discovered_app_id=member.discovered_app_id
               left join research_notes note
                 on note.discovered_developer_id=developer.id
               left join research_lists list on list.id=list_app.research_list_id
               where (list.status='active' or note.id is not null)
                 and (developer.last_scanned_at is null
                      or developer.last_scanned_at < now()-interval '23 hours')
               order by developer.id limit %s""",
            (limit,),
        ).fetchall()
    ]
