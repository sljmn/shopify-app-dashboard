"""Is the pipeline actually running?

Every number on the dashboard is only as fresh as the last successful Partner
API poll, and a stalled scheduler looks exactly like a quiet week: the charts
keep rendering, they just stop changing. The staleness rule lives here once, so
the strip on Overview and the Slack warning can never disagree about what
"stale" means.
"""

import logging
from datetime import datetime, timezone

import httpx

from app_dashboard.catalog import AppConfig
from app_dashboard.pipeline import SOURCE
from app_dashboard.scope import Scope

logger = logging.getLogger(__name__)

# Both thresholds are counted in missed polls, not in fixed minutes, so they
# stay correct when POLL_INTERVAL_MINUTES changes. A fixed 60-minute alert was
# fine at a 15-minute interval and would have fired on a single skipped poll at
# 30. The page goes red first and Slack shouts one poll later, so a stall is
# visible before it is noisy.
PAGE_STALE_POLLS = 3
ALERT_STALE_POLLS = 4


def sync_health(
    conn, poll_interval_minutes: int, scope: Scope = Scope.all()
) -> dict:
    app_predicate = "true" if scope.app_id is None else "a.id = %s"
    app_params = () if scope.app_id is None else (scope.app_id,)
    rows = conn.execute(
        f"""select a.id, a.slug, a.name, s.last_synced_at
            from apps a left join sync_state s
              on s.app_id = a.id and s.source = %s
            where a.active and {app_predicate}
            order by a.name""",
        (SOURCE, *app_params),
    ).fetchall()
    last_values = [row[3] for row in rows]
    last = min(last_values) if last_values and all(last_values) else None
    age = None
    if last is not None:
        age = (datetime.now(timezone.utc) - last).total_seconds() / 60

    event_predicate, event_params = scope.predicate("raw_app_events")
    events_24h = conn.execute(
        f"""select count(*) from raw_app_events
            where ingested_at >= now() - interval '24 hours' and {event_predicate}""",
        event_params,
    ).fetchone()[0]
    shop_predicate, shop_params = scope.predicate("shops")
    shops = conn.execute(
        f"select count(*) from shops where {shop_predicate}", shop_params
    ).fetchone()[0]

    page_threshold = PAGE_STALE_POLLS * poll_interval_minutes
    now = datetime.now(timezone.utc)
    app_health = []
    for app_id, slug, name, synced_at in rows:
        app_age = (
            None
            if synced_at is None
            else (now - synced_at).total_seconds() / 60
        )
        app_health.append({
            "app_id": app_id,
            "slug": slug,
            "name": name,
            "last_synced_at": synced_at,
            "age_minutes": None if app_age is None else round(app_age),
            "stale": app_age is None or app_age > page_threshold,
        })
    return {
        "last_synced_at": last,
        "age_minutes": None if age is None else round(age),
        # A sync that has never run is stale: the alternative is a green strip
        # on a machine whose scheduler never started.
        "stale": age is None or age > page_threshold,
        "page_threshold_minutes": page_threshold,
        "events_24h": events_24h,
        "shops": shops,
        "apps": app_health,
        "stale_apps": sum(1 for item in app_health if item["stale"]),
    }


def build_stale_message(age_minutes: int | None, base_url: str,
                        dashboard_name: str = "Analytics") -> dict:
    when = "never" if age_minutes is None else f"{age_minutes} minutes ago"
    return {"text": (
        f":rotating_light: {dashboard_name} sync is stale. Last successful "
        f"Partner API poll: {when}. Every number on {base_url} is frozen until it "
        f"recovers."
    )}


def check_stale_sync(
    conn, app: AppConfig, settings, http_post=httpx.post
) -> bool:
    """Warn once per stale episode, not once per poll.

    The flag is stored next to the cursor rather than in memory so a machine
    restart (the most likely reason a sync stalls in the first place) cannot
    turn one outage into an alert on every poll.
    """
    threshold = ALERT_STALE_POLLS * settings.poll_interval_minutes
    row = conn.execute(
        """select last_synced_at, stale_alerted_at from sync_state
           where app_id = %s and source = %s""",
        (app.id, SOURCE),
    ).fetchone()
    last, alerted_at = row if row else (None, None)

    age = None
    if last is not None:
        age = (datetime.now(timezone.utc) - last).total_seconds() / 60

    if age is not None and age <= threshold:
        if alerted_at is not None:
            logger.info("sync recovered after a stale episode")
            conn.execute(
                """update sync_state set stale_alerted_at = null
                   where app_id = %s and source = %s""",
                (app.id, SOURCE),
            )
            conn.commit()
        return False

    if alerted_at is not None:
        return False   # already shouted about this episode

    if not settings.slack_webhook_url:
        logger.warning("sync is stale but SLACK_WEBHOOK_URL is unset")
        return False

    from app_dashboard.slack import post_alert

    payload = build_stale_message(None if age is None else round(age),
                                  settings.public_base_url,
                                  f"{settings.dashboard_name}: {app.name}")
    if not post_alert(settings.slack_webhook_url, payload, http_post=http_post):
        return False   # leave the flag unset so the next poll retries

    conn.execute(
        """
        insert into sync_state (app_id, source, stale_alerted_at)
        values (%s, %s, now())
        on conflict (app_id, source) do update set stale_alerted_at = now()
        """,
        (app.id, SOURCE),
    )
    conn.commit()
    logger.warning("posted stale-sync warning to Slack")
    return True
