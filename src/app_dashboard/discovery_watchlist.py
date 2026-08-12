"""Follow state, immutable listing history, and reports for public apps."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

from psycopg.types.json import Jsonb

from app_dashboard.app_store_discovery import growth_signals
from app_dashboard.listing_intelligence import LISTING_FIELDS, listing_hash

FOLLOW_SOURCES = frozenset({"manual", "rising_gem", "new_contender"})


@dataclass(frozen=True)
class WatchStatus:
    active: bool
    follow_source: str
    followed_at: datetime
    unfollowed_at: datetime | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None


@dataclass(frozen=True)
class CompetitorSnapshotResult:
    snapshot_id: int
    created: bool
    changed_fields: tuple[str, ...]


def _status(row) -> WatchStatus | None:
    return WatchStatus(*row) if row else None


def watch_status(conn, handle: str) -> WatchStatus | None:
    return _status(conn.execute(
        """select watch.active,watch.follow_source,watch.followed_at,
                  watch.unfollowed_at,watch.last_attempt_at,watch.last_success_at,
                  watch.last_error_code
           from discovery_watchlist watch
           join discovered_apps app on app.id=watch.discovered_app_id
           where app.handle=%s""",
        (handle,),
    ).fetchone())


def follow_app(conn, handle: str, *, source: str, now=None) -> WatchStatus:
    if source not in FOLLOW_SOURCES:
        raise ValueError("invalid-follow-source")
    followed_at = now or datetime.now(timezone.utc)
    app = conn.execute(
        "select id from discovered_apps where handle=%s", (handle,)
    ).fetchone()
    if not app:
        raise LookupError("unknown-discovered-app")
    conn.execute(
        """insert into discovery_watchlist
             (discovered_app_id,active,follow_source,followed_at)
           values (%s,true,%s,%s)
           on conflict (discovered_app_id) do update set
             active=true,unfollowed_at=null""",
        (app[0], source, followed_at),
    )
    return watch_status(conn, handle)


def unfollow_app(conn, handle: str, *, now=None) -> WatchStatus:
    unfollowed_at = now or datetime.now(timezone.utc)
    result = conn.execute(
        """update discovery_watchlist watch set active=false,unfollowed_at=%s
           from discovered_apps app
           where app.id=watch.discovered_app_id and app.handle=%s
           returning watch.active,watch.follow_source,watch.followed_at,
                     watch.unfollowed_at,watch.last_attempt_at,
                     watch.last_success_at,watch.last_error_code""",
        (unfollowed_at, handle),
    ).fetchone()
    if not result:
        raise LookupError("app-not-followed")
    return _status(result)


def follow_automatic_candidates(conn, *, now=None) -> dict:
    observed_at = now or datetime.now(timezone.utc)
    signals = growth_signals(conn, now=observed_at, limit=100_000)
    candidates = {}
    for row in signals["contenders"]:
        candidates[row["handle"]] = "new_contender"
    for row in signals["gems"]:
        candidates[row["handle"]] = "rising_gem"
    followed = 0
    for handle, source in candidates.items():
        result = conn.execute(
            """insert into discovery_watchlist
                 (discovered_app_id,active,follow_source,followed_at)
               select id,true,%s,%s from discovered_apps where handle=%s
               on conflict (discovered_app_id) do nothing
               returning discovered_app_id""",
            (source, observed_at, handle),
        ).fetchone()
        followed += int(bool(result))
    return {"followed": followed, "already_followed": len(candidates) - followed}


def active_watched_apps(conn) -> list[tuple[int, str]]:
    return conn.execute(
        """select app.id,app.handle from discovery_watchlist watch
           join discovered_apps app on app.id=watch.discovered_app_id
           where watch.active order by app.handle"""
    ).fetchall()


def record_scan_failure(conn, discovered_app_id: int, attempted_at, code: str):
    conn.execute(
        """update discovery_watchlist
           set last_attempt_at=%s,last_error_code=%s
           where discovered_app_id=%s""",
        (attempted_at, code, discovered_app_id),
    )


def store_competitor_snapshot(
    conn, discovered_app_id: int, listing: dict,
    media: Sequence[tuple[str, int, str, object]], captured_at,
) -> CompetitorSnapshotResult:
    content_hash = listing_hash(listing)
    existing = conn.execute(
        """select id from discovery_listing_snapshots
           where discovered_app_id=%s and content_hash=%s""",
        (discovered_app_id, content_hash),
    ).fetchone()
    if existing:
        conn.execute(
            """update discovery_watchlist
               set last_attempt_at=%s,last_success_at=%s,last_error_code=null
               where discovered_app_id=%s""",
            (captured_at, captured_at, discovered_app_id),
        )
        return CompetitorSnapshotResult(existing[0], False, ())
    previous = conn.execute(
        """select listing from discovery_listing_snapshots
           where discovered_app_id=%s order by captured_at desc,id desc limit 1""",
        (discovered_app_id,),
    ).fetchone()
    changed_fields = tuple(
        field for field in LISTING_FIELDS
        if previous and previous[0].get(field) != listing.get(field)
    )
    with conn.transaction():
        snapshot_id = conn.execute(
            """insert into discovery_listing_snapshots
               (discovered_app_id,captured_at,content_hash,listing)
               values (%s,%s,%s,%s) returning id""",
            (discovered_app_id, captured_at, content_hash, Jsonb(listing)),
        ).fetchone()[0]
        if previous:
            conn.cursor().executemany(
                """insert into discovery_listing_changes
                   (discovered_app_id,snapshot_id,changed_at,field,
                    before_value,after_value)
                   values (%s,%s,%s,%s,%s,%s)""",
                [
                    (discovered_app_id, snapshot_id, captured_at, field,
                     Jsonb(previous[0].get(field)), Jsonb(listing.get(field)))
                    for field in changed_fields
                ],
            )
        for role, position, source_url, archived in media:
            conn.execute(
                """insert into discovery_media_objects
                   (digest,object_key,mime_type,byte_size,width,height,created_at)
                   values (%s,%s,%s,%s,%s,%s,%s) on conflict (digest) do nothing""",
                (archived.digest, archived.object_key, archived.mime_type,
                 archived.byte_size, archived.width, archived.height, captured_at),
            )
            conn.execute(
                """insert into discovery_snapshot_media
                   (snapshot_id,digest,role,position,source_url)
                   values (%s,%s,%s,%s,%s)""",
                (snapshot_id, archived.digest, role, position, source_url),
            )
        conn.execute(
            """update discovery_watchlist
               set last_attempt_at=%s,last_success_at=%s,last_error_code=null
               where discovered_app_id=%s""",
            (captured_at, captured_at, discovered_app_id),
        )
    return CompetitorSnapshotResult(snapshot_id, True, changed_fields)


def app_detail(conn, handle: str) -> dict | None:
    row = conn.execute(
        """select app.id,app.handle,app.display_name,app.first_seen_at,
                  app.listing_updated_on,coalesce(watch.active,false),
                  watch.follow_source,watch.followed_at,watch.last_success_at,
                  watch.last_error_code,observation.review_count,
                  observation.rating,observation.best_category_rank,
                  snapshot.id,snapshot.captured_at,snapshot.listing,
                  coalesce(string_agg(distinct category.name, ', '
                    order by category.name),'') categories
           from discovered_apps app
           left join discovery_watchlist watch on watch.discovered_app_id=app.id
           left join lateral (
             select review_count,rating,best_category_rank
             from discovery_app_observations where discovered_app_id=app.id
             order by observed_on desc limit 1
           ) observation on true
           left join lateral (
             select id,captured_at,listing from discovery_listing_snapshots
             where discovered_app_id=app.id order by captured_at desc,id desc limit 1
           ) snapshot on true
           left join discovered_app_categories member on member.discovered_app_id=app.id
           left join discovery_categories category on category.id=member.category_id
           where app.handle=%s
           group by app.id,watch.active,watch.follow_source,watch.followed_at,
                    watch.last_success_at,watch.last_error_code,
                    observation.review_count,observation.rating,
                    observation.best_category_rank,snapshot.id,snapshot.captured_at,
                    snapshot.listing""",
        (handle,),
    ).fetchone()
    if not row:
        return None
    keys = (
        "id", "handle", "name", "first_seen_at", "listing_updated_on",
        "followed", "follow_source", "followed_at", "last_success_at",
        "last_error_code", "reviews", "rating", "best_rank", "snapshot_id",
        "snapshot_at", "listing", "categories",
    )
    return dict(zip(keys, row, strict=True))


def growth_history(conn, discovered_app_id: int) -> dict:
    reviews = conn.execute(
        """select observed_on,review_count,rating,best_category_rank
           from discovery_app_observations where discovered_app_id=%s
           order by observed_on""",
        (discovered_app_id,),
    ).fetchall()
    ranks = conn.execute(
        """select observation.observed_on,category.name,observation.position
           from discovery_category_observations observation
           join discovery_categories category on category.id=observation.category_id
           where observation.discovered_app_id=%s
           order by observation.observed_on,category.name""",
        (discovered_app_id,),
    ).fetchall()
    return {"reviews": reviews, "ranks": ranks}


def listing_versions(conn, discovered_app_id: int) -> list[dict]:
    rows = conn.execute(
        """select snapshot.id,snapshot.captured_at,snapshot.listing,
                  coalesce(array_agg(change.field order by change.field)
                    filter (where change.field is not null),'{}') fields,
                  count(distinct media.digest) media_count,
                  next_observation.review_count-prior_observation.review_count
                    review_movement,
                  prior_observation.best_category_rank-
                    next_observation.best_category_rank rank_movement
           from discovery_listing_snapshots snapshot
           left join discovery_listing_changes change on change.snapshot_id=snapshot.id
           left join discovery_snapshot_media media on media.snapshot_id=snapshot.id
           left join lateral (
             select review_count,best_category_rank
             from discovery_app_observations
             where discovered_app_id=snapshot.discovered_app_id
               and observed_on <= snapshot.captured_at::date
             order by observed_on desc limit 1
           ) prior_observation on true
           left join lateral (
             select review_count,best_category_rank
             from discovery_app_observations
             where discovered_app_id=snapshot.discovered_app_id
               and observed_on > snapshot.captured_at::date
             order by observed_on limit 1
           ) next_observation on true
           where snapshot.discovered_app_id=%s
           group by snapshot.id,prior_observation.review_count,
                    prior_observation.best_category_rank,
                    next_observation.review_count,
                    next_observation.best_category_rank
           order by snapshot.captured_at desc,snapshot.id desc""",
        (discovered_app_id,),
    ).fetchall()
    return [
        {"id": row[0], "captured_at": row[1], "listing": row[2],
         "changed_fields": row[3], "media_count": row[4],
         "review_movement": row[5], "rank_movement": row[6]}
        for row in rows
    ]


def _version(conn, discovered_app_id: int, snapshot_id: int) -> dict | None:
    row = conn.execute(
        """select id,captured_at,listing from discovery_listing_snapshots
           where id=%s and discovered_app_id=%s""",
        (snapshot_id, discovered_app_id),
    ).fetchone()
    if not row:
        return None
    media = conn.execute(
        """select media.role,media.position,media.digest,media.source_url
           from discovery_snapshot_media media where media.snapshot_id=%s
           order by media.role,media.position""",
        (snapshot_id,),
    ).fetchall()
    return {"id": row[0], "captured_at": row[1], "listing": row[2], "media": media}


def compare_versions(
    conn, discovered_app_id: int, before_id: int, after_id: int,
) -> dict | None:
    before = _version(conn, discovered_app_id, before_id)
    after = _version(conn, discovered_app_id, after_id)
    if not before or not after:
        return None
    fields = []
    for field in LISTING_FIELDS:
        old = before["listing"].get(field)
        new = after["listing"].get(field)
        if old != new:
            fields.append({"field": field, "before": old, "after": new})
    return {"before": before, "after": after, "fields": fields}


def list_watched_apps(conn, *, page: int = 1, per_page: int = 100) -> dict:
    total = conn.execute(
        "select count(*) from discovery_watchlist where active"
    ).fetchone()[0]
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    rows = conn.execute(
        """select app.handle,app.display_name,watch.follow_source,
                  watch.followed_at,watch.last_success_at,watch.last_error_code,
                  observation.review_count,observation.best_category_rank,
                  change.changed_at,change.field
           from discovery_watchlist watch
           join discovered_apps app on app.id=watch.discovered_app_id
           left join lateral (
             select review_count,best_category_rank
             from discovery_app_observations where discovered_app_id=app.id
             order by observed_on desc limit 1
           ) observation on true
           left join lateral (
             select changed_at,field from discovery_listing_changes
             where discovered_app_id=app.id order by changed_at desc,id desc limit 1
           ) change on true
           where watch.active
           order by coalesce(change.changed_at,watch.followed_at) desc,app.handle
           limit %s offset %s""",
        (per_page, (page - 1) * per_page),
    ).fetchall()
    keys = (
        "handle", "name", "follow_source", "followed_at", "last_success_at",
        "last_error_code", "reviews", "best_rank", "last_change_at",
        "last_change_field",
    )
    return {"rows": [dict(zip(keys, row, strict=True)) for row in rows], "total": total,
            "page": page, "pages": pages}


def watchlist_summary(conn, start: date, end: date) -> dict:
    new_follows = conn.execute(
        """select app.handle,app.display_name,watch.follow_source,watch.followed_at
           from discovery_watchlist watch join discovered_apps app
             on app.id=watch.discovered_app_id
           where (watch.followed_at at time zone 'Europe/Amsterdam')::date
             between %s and %s
           order by watch.followed_at desc""",
        (start, end),
    ).fetchall()
    review_gainers = conn.execute(
        """with bounds as (
             select watch.discovered_app_id,
               (select review_count from discovery_app_observations o
                where o.discovered_app_id=watch.discovered_app_id
                  and o.observed_on <= %s order by observed_on desc limit 1) latest,
               (select review_count from discovery_app_observations o
                where o.discovered_app_id=watch.discovered_app_id
                  and o.observed_on < %s order by observed_on desc limit 1) previous
             from discovery_watchlist watch where watch.active
           )
           select app.handle,app.display_name,bounds.latest-bounds.previous gain
           from bounds join discovered_apps app on app.id=bounds.discovered_app_id
           where bounds.latest is not null and bounds.previous is not null
             and bounds.latest > bounds.previous
           order by gain desc limit 20""",
        (end, start),
    ).fetchall()
    rank_gainers = conn.execute(
        """with bounds as (
             select watch.discovered_app_id,
               (select best_category_rank from discovery_app_observations o
                where o.discovered_app_id=watch.discovered_app_id
                  and o.observed_on <= %s order by observed_on desc limit 1) latest,
               (select best_category_rank from discovery_app_observations o
                where o.discovered_app_id=watch.discovered_app_id
                  and o.observed_on < %s order by observed_on desc limit 1) previous
             from discovery_watchlist watch where watch.active
           )
           select app.handle,app.display_name,bounds.previous-bounds.latest gain
           from bounds join discovered_apps app on app.id=bounds.discovered_app_id
           where bounds.latest is not null and bounds.previous is not null
             and bounds.latest < bounds.previous
           order by gain desc limit 20""",
        (end, start),
    ).fetchall()
    changes = conn.execute(
        """select app.handle,app.display_name,change.changed_at,change.field
           from discovery_listing_changes change join discovered_apps app
             on app.id=change.discovered_app_id
           where (change.changed_at at time zone 'Europe/Amsterdam')::date
             between %s and %s
           order by change.changed_at desc,app.handle,change.field""",
        (start, end),
    ).fetchall()
    patterns = conn.execute(
        """select field,count(*) from discovery_listing_changes
           where (changed_at at time zone 'Europe/Amsterdam')::date
             between %s and %s
           group by field order by count(*) desc,field""",
        (start, end),
    ).fetchall()
    health = conn.execute(
        """select count(*) filter (where active),
                  count(*) filter (where active and last_error_code is not null),
                  count(*) filter (where active and last_success_at is not null)
           from discovery_watchlist"""
    ).fetchone()
    return {
        "new_follows": new_follows, "review_gainers": review_gainers,
        "rank_gainers": rank_gainers, "listing_changes": changes,
        "patterns": patterns,
        "health": {"active": health[0], "failed": health[1], "scanned": health[2]},
    }
