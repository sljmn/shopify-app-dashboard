"""The Monday digest.

One Slack message a week that answers "are we on pace to 500?" without anyone
opening the dashboard. Reading it should take ten seconds; every line is a
number plus its week-over-week change.

Collecting and rendering are separate on purpose: `render_digest` is pure, so
the wording is testable against a fixture without a database or a webhook.
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from app_dashboard.scope import Scope
from app_dashboard.slack import escape

logger = logging.getLogger(__name__)

DIGEST_SOURCE = "weekly_digest"
# A digest is only worth sending once a week. The guard is a floor, not a
# schedule: it stops a machine restart on Monday morning, a manual run, or a
# history replay from firing a second (or historical) digest.
MIN_DAYS_BETWEEN_DIGESTS = 3
TRIAL_WATCH_IN_DIGEST = 3


def collect_digest(conn, now=None, scope: Scope = Scope.all()) -> dict:
    from app_dashboard.stats import (
        mrr_at,
        mrr_movement_between,
        paying_at,
        review_candidates,
        trial_watch,
    )

    now = now or datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    def count(sql, params):
        return conn.execute(sql, params).fetchone()[0]

    event_predicate, event_params = scope.predicate("app_events")
    installs = count(
        f"""select count(*) from app_events where type in ('installed', 'reinstalled')
            and occurred_at >= %s and {event_predicate}""",
        (week_ago, *event_params))
    uninstalls = count(
        f"""select count(*) from app_events where type = 'uninstalled'
            and occurred_at >= %s and {event_predicate}""",
        (week_ago, *event_params))
    shop_predicate, shop_params = scope.predicate("shops")
    installed = count(
        f"select count(*) from shops where install_state = 'installed' and {shop_predicate}",
        shop_params,
    )

    # GA4 is day-grained and today is always partial, so both traffic windows
    # are seven whole days ending yesterday. Lifecycle counts above are
    # instant-grained and do include today; they come from a different source
    # and would be wrong to truncate.
    ga_predicate, ga_params = scope.predicate("ga4_daily")
    sessions, installs_ga4 = conn.execute(
        f"""select coalesce(sum(sessions), 0), coalesce(sum(installs), 0)
            from ga4_daily where dimension = 'total' and date >= %s and date < %s
              and {ga_predicate}""",
        (week_ago.date(), now.date(), *ga_params)).fetchone()
    prior_sessions = conn.execute(
        f"""select coalesce(sum(sessions), 0) from ga4_daily
            where dimension = 'total' and date >= %s and date < %s
              and {ga_predicate}""",
        (two_weeks_ago.date(), week_ago.date(), *ga_params)).fetchone()[0]

    return {
        "installed": installed,
        # Net change in the installed base is the week's events, not a stored
        # historical count: shops carries only current state.
        "installed_delta": installs - uninstalls,
        "installs": installs,
        "uninstalls": uninstalls,
        "paying": paying_at(conn, now, scope),
        "paying_delta": paying_at(conn, now, scope) - paying_at(conn, week_ago, scope),
        "mrr": mrr_at(conn, now, scope),
        "mrr_delta": mrr_at(conn, now, scope) - mrr_at(conn, week_ago, scope),
        "movement": mrr_movement_between(conn, week_ago, now, scope),
        "trial_watch": trial_watch(conn, scope=scope)[:TRIAL_WATCH_IN_DIGEST],
        "trial_watch_total": len(trial_watch(conn, scope=scope)),
        "review_candidates": len(review_candidates(conn, scope=scope)),
        "sessions": sessions,
        "sessions_delta": sessions - prior_sessions,
        "listing_installs": installs_ga4,
    }


def _signed(value) -> str:
    return f"+{value}" if value > 0 else str(value)


def _money(value) -> str:
    return f"${Decimal(value):.2f}"


def render_digest(data: dict, app_name: str = "The app") -> str:
    """Plain Slack markdown, one message, no threads.

    Shop names appear (this is an internal channel and a call sheet is useless
    without them); merchant emails and owner names never do.
    """
    move = data["movement"]
    lines = [
        f"*{app_name}, last 7 days*",
        f"Installed: *{data['installed']}* ({_signed(data['installed_delta'])})"
        f"  ·  Paying: *{data['paying']}* ({_signed(data['paying_delta'])})"
        f"  ·  MRR: *{_money(data['mrr'])}* ({_signed(round(data['mrr_delta']))})",
        f"Installs {data['installs']}, uninstalls {data['uninstalls']}.",
    ]

    parts = []
    for kind in ("new", "reactivation", "expansion", "contraction", "churned"):
        if move[kind]:
            parts.append(f"{kind} {_signed(round(move[kind]))}")
    lines.append(
        f"MRR movement: {', '.join(parts)} = {_signed(round(move['net']))}."
        if parts else "MRR movement: nothing moved."
    )

    if data["sessions"]:
        rate = round(100 * data["listing_installs"] / data["sessions"], 1)
        lines.append(
            f"Listing: *{data['sessions']}* sessions ({_signed(data['sessions_delta'])}), "
            f"{data['listing_installs']} installs, {rate}% of sessions."
        )
    else:
        lines.append("Listing: no GA4 sessions recorded this week.")

    if data["trial_watch"]:
        # Shop names are merchant-controlled and this string is posted to Slack
        # as mrkdwn; see app_dashboard.slack.escape.
        names = ", ".join(
            f"{escape(s['shop'])} ({s['days']}d)" for s in data["trial_watch"]
        )
        lines.append(
            f"Trial watch ({data['trial_watch_total']}): {names}."
            + (" More on the Actions page." if data["trial_watch_total"]
               > len(data["trial_watch"]) else "")
        )
    else:
        lines.append("Trial watch: nobody installed recently is still unsubscribed.")

    lines.append(f"{data['review_candidates']} merchants are due a review ask.")
    return "\n".join(lines)


def should_send(last_sent, now=None) -> bool:
    if last_sent is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - last_sent) >= timedelta(days=MIN_DAYS_BETWEEN_DIGESTS)


def send_weekly_digest(conn, apps, settings, http_post=httpx.post, now=None) -> bool:
    from app_dashboard.slack import post_alert

    row = conn.execute(
        "select last_run_at from operations_state where source = %s", (DIGEST_SOURCE,)
    ).fetchone()
    if not should_send(row[0] if row else None, now):
        logger.info("weekly digest already sent recently; skipping")
        return False
    if not settings.slack_webhook_url:
        logger.info("SLACK_WEBHOOK_URL unset; skipping weekly digest")
        return False

    aggregate = collect_digest(conn, now)
    text = render_digest(aggregate, "All Shopify apps")
    app_lines = []
    for app in apps:
        data = collect_digest(conn, now, Scope.for_app(app.id))
        app_lines.append(
            f"{app.name}: {_money(data['mrr'])} MRR, {data['installed']} installed, "
            f"{data['installs']} installs / {data['uninstalls']} uninstalls."
        )
    if app_lines:
        text += "\n\n*By app*\n" + "\n".join(app_lines)
    if not post_alert(settings.slack_webhook_url, {"text": text}, http_post=http_post):
        return False   # no timestamp written, so the next run retries

    conn.execute(
        """insert into operations_state (source, last_run_at) values (%s, now())
           on conflict (source) do update set last_run_at = now()""",
        (DIGEST_SOURCE,),
    )
    conn.commit()
    return True
