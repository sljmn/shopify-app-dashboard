"""Category-relative intelligence from captured public App Store reviews."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median

from app_dashboard.app_store_discovery import pricing_profile

PRESETS = frozenset({"gems", "unexpected", "established", "all"})
PERIODS = frozenset({7, 30, 90})


def percentile(value: int, population: list[int]) -> float:
    if not population:
        return 0.0
    return sum(item <= value for item in population) / len(population)


def score_candidate(
    *, reviews: int, recent: int, previous: int, rating: float | None,
    rank: int | None, category_reviews: list[int], category_recent: list[int],
    category_apps: int, active_grower_share: float, top_ten_concentration: float,
    observation_count: int, backfill_complete: bool,
    last_success_at: datetime | None, now: datetime,
) -> dict:
    review_percentile = percentile(reviews, category_reviews)
    velocity_percentile = percentile(recent, category_recent)
    acceleration = recent - previous
    established = category_apps >= 5 and review_percentile >= 0.9
    unexpected = (
        category_apps >= 5 and not established and recent > 0
        and velocity_percentile >= 0.8
    )
    size_opportunity = max(0.0, min(1.0, 1 - category_apps / 500))
    opportunity = (
        size_opportunity * 0.50
        + (1 - active_grower_share) * 0.30
        + (1 - top_ten_concentration) * 0.20
    )
    acceleration_score = max(0.0, min(1.0, acceleration / max(recent, 1)))
    quality = max(0.0, min(1.0, ((rating or 0) - 3) / 2))
    rank_score = max(0.0, min(1.0, (101 - (rank or 101)) / 100))
    gem_score = round(100 * (
        velocity_percentile * 0.42
        + acceleration_score * 0.20
        + opportunity * 0.16
        + quality * 0.12
        + rank_score * 0.10
    ))
    confidence = 20
    confidence += min(observation_count, 3) * 15
    confidence += 25 if backfill_complete else 0
    if last_success_at and now - last_success_at <= timedelta(days=7):
        confidence += 10
    return {
        "review_percentile": round(review_percentile * 100),
        "velocity_percentile": round(velocity_percentile * 100),
        "acceleration": acceleration,
        "established": established,
        "unexpected": unexpected,
        "active_grower_share": round(active_grower_share * 100),
        "top_ten_concentration": round(top_ten_concentration * 100),
        "gem_score": max(0, min(gem_score, 100)),
        "confidence": max(0, min(confidence, 100)),
    }


def review_intelligence_report(
    conn, *, period: int = 30, category: str = "", preset: str = "gems",
    rating: int | None = None, pricing: str = "", bfs: str = "",
    page: int = 1, per_page: int = 50, now=None,
) -> dict:
    period = period if period in PERIODS else 30
    preset = preset if preset in PRESETS else "gems"
    rating = rating if rating in {1, 2, 3, 4, 5} else None
    current = now or datetime.now(UTC)
    today = current.date()
    raw = conn.execute(
        """with latest as (
             select distinct on (discovered_app_id)
               discovered_app_id,review_count,rating,best_category_rank
             from discovery_app_observations
             order by discovered_app_id,observed_on desc
           ), counts as (
             select discovered_app_id,
               count(*) filter (where reviewed_on >= %s) recent,
               count(*) filter (where reviewed_on < %s and reviewed_on >= %s) previous
             from discovery_reviews group by discovered_app_id
           ), observation_depth as (
             select discovered_app_id,count(*) observations
             from discovery_app_observations group by discovered_app_id
           ), categories as (
             select member.discovered_app_id,category.slug,category.name
             from discovered_app_categories member
             join discovery_categories category on category.id=member.category_id
           )
           select app.id,app.handle,coalesce(app.display_name,app.handle),
                  app.built_for_shopify,app.icon_digest,
                  latest.review_count,latest.rating,
                  latest.best_category_rank,coalesce(counts.recent,0),
                  coalesce(counts.previous,0),coalesce(depth.observations,0),
                  state.backfill_completed_at is not null,state.last_success_at,
                  coalesce(jsonb_agg(distinct jsonb_build_object(
                    'slug',categories.slug,'name',categories.name))
                    filter (where categories.slug is not null),'[]'::jsonb)
           from discovered_apps app
           left join latest on latest.discovered_app_id=app.id
           left join counts on counts.discovered_app_id=app.id
           left join observation_depth depth on depth.discovered_app_id=app.id
           left join discovery_review_sync_state state on state.discovered_app_id=app.id
           left join categories on categories.discovered_app_id=app.id
           where app.delisted_at is null and latest.discovered_app_id is not null
           group by app.id,latest.review_count,latest.rating,
                    latest.best_category_rank,counts.recent,counts.previous,
                    depth.observations,state.backfill_completed_at,
                    state.last_success_at""",
        (today - timedelta(days=period), today - timedelta(days=period),
         today - timedelta(days=period * 2)),
    ).fetchall()
    keys = (
        "id", "handle", "name", "bfs", "icon_digest", "reviews", "rating", "rank",
        "recent", "previous", "observations", "backfill_complete",
        "last_success_at", "categories",
    )
    rows = [dict(zip(keys, item, strict=True)) for item in raw]
    by_category: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        for item in row["categories"]:
            by_category[item["slug"]].append(row)
    category_context = {}
    for slug, peers in by_category.items():
        peer_reviews = [peer["reviews"] or 0 for peer in peers]
        peer_recent = [peer["recent"] for peer in peers]
        total_reviews = sum(peer_reviews)
        category_context[slug] = {
            "peers": peers,
            "reviews": peer_reviews,
            "recent": peer_recent,
            "active_grower_share": (
                sum(value > 0 for value in peer_recent) / len(peers)
            ),
            "top_ten_concentration": (
                sum(sorted(peer_reviews, reverse=True)[:10]) / total_reviews
                if total_reviews else 0.0
            ),
            "median_reviews": int(median(peer_reviews)),
        }
    scoring_rows = (
        rows if preset in {"all", "established"}
        else [row for row in rows if row["recent"] > 0]
    )
    for row in scoring_rows:
        candidates = []
        score_categories = [
            item for item in row["categories"]
            if not category or item["slug"] == category
        ] or [{"slug": "", "name": "Uncategorized"}]
        for item in score_categories:
            context = category_context.get(item["slug"])
            if context is None:
                context = {
                    "peers": [row], "reviews": [row["reviews"] or 0],
                    "recent": [row["recent"]],
                    "active_grower_share": int(row["recent"] > 0),
                    "top_ten_concentration": 1.0,
                    "median_reviews": row["reviews"] or 0,
                }
            score = score_candidate(
                reviews=row["reviews"] or 0, recent=row["recent"],
                previous=row["previous"], rating=float(row["rating"] or 0),
                rank=row["rank"],
                category_reviews=context["reviews"],
                category_recent=context["recent"],
                category_apps=len(context["peers"]),
                active_grower_share=context["active_grower_share"],
                top_ten_concentration=context["top_ten_concentration"],
                observation_count=row["observations"],
                backfill_complete=row["backfill_complete"],
                last_success_at=row["last_success_at"], now=current,
            )
            score.update({
                "category_slug": item["slug"], "category_name": item["name"],
                "category_apps": len(context["peers"]),
                "category_median_reviews": context["median_reviews"],
            })
            candidates.append(score)
        row.update(max(candidates, key=lambda item: item["gem_score"]))
        row["pricing"] = {"label": "Unknown"}
    rows = scoring_rows

    if pricing and rows:
        listings = {
            app_id: listing for app_id, listing in conn.execute(
                """select distinct on (discovered_app_id)
                         discovered_app_id,listing
                   from discovery_listing_snapshots
                   where discovered_app_id=any(%s)
                   order by discovered_app_id,captured_at desc,id desc""",
                ([row["id"] for row in rows],),
            ).fetchall()
        }
        for row in rows:
            row["pricing"] = pricing_profile(listings.get(row["id"]))

    def included(row):
        if category and not any(item["slug"] == category for item in row["categories"]):
            return False
        if bfs == "bfs" and row["bfs"] is not True:
            return False
        if bfs == "not_bfs" and row["bfs"] is not False:
            return False
        if pricing and row["pricing"]["label"].lower().replace(" + ", "_") != pricing:
            return False
        if preset == "gems":
            return (
                row["category_apps"] >= 5 and row["recent"] > 0
                and not row["established"]
            )
        if preset == "unexpected":
            return row["unexpected"]
        if preset == "established":
            return row["established"]
        return True

    filtered = [row for row in rows if included(row)]
    filtered.sort(
        key=lambda row: (row["gem_score"], row["recent"], row["reviews"] or 0),
        reverse=True,
    )
    total = len(filtered)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    filtered = filtered[(page - 1) * per_page:page * per_page]
    feed_where = ["review.reviewed_on >= %s"]
    feed_params: list = [today - timedelta(days=period)]
    if category:
        feed_where.append(
            "exists (select 1 from discovered_app_categories member join "
            "discovery_categories category on category.id=member.category_id "
            "where member.discovered_app_id=app.id and category.slug=%s)"
        )
        feed_params.append(category)
    if rating:
        feed_where.append("review.rating=%s")
        feed_params.append(rating)
    feed = conn.execute(
        f"""select review.shopify_review_id,review.rating,review.reviewed_on,
                    review.merchant_name,review.country,review.body,
                    review.developer_reply,review.source_url,app.handle,
                    coalesce(app.display_name,app.handle),app.icon_digest
             from discovery_reviews review
             join discovered_apps app on app.id=review.discovered_app_id
             where {' and '.join(feed_where)}
             order by review.reviewed_on desc,review.shopify_review_id desc
             limit 100""",
        feed_params,
    ).fetchall()
    feed_keys = (
        "id", "rating", "reviewed_on", "merchant", "country", "body",
        "reply", "source_url", "handle", "app_name", "icon_digest",
    )
    categories = conn.execute(
        """select category.slug,category.name,count(*)
           from discovery_categories category
           join discovered_app_categories member on member.category_id=category.id
           group by category.id order by category.name"""
    ).fetchall()
    coverage = conn.execute(
        """select count(*) filter (where app.delisted_at is null),
                  count(*) filter (where app.delisted_at is null and state.last_success_at is not null),
                  count(*) filter (where app.delisted_at is null and state.backfill_completed_at is not null),
                  (select count(*) from discovery_reviews)
           from discovered_apps app
           left join discovery_review_sync_state state on state.discovered_app_id=app.id"""
    ).fetchone()
    return {
        "rows": filtered, "feed": [dict(zip(feed_keys, item, strict=True)) for item in feed],
        "total": total, "page": page, "pages": pages, "period": period,
        "preset": preset, "category": category, "rating": rating,
        "pricing_filter": pricing, "bfs_filter": bfs, "categories": categories,
        "coverage": {"apps": coverage[0], "checked": coverage[1],
                     "complete": coverage[2], "reviews": coverage[3]},
    }
