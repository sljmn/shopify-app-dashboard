"""Current Shopify trials, kept separate from paid revenue reporting."""

from datetime import datetime, timezone
from decimal import Decimal
from math import ceil

import psycopg
from psycopg.rows import dict_row

from app_dashboard.active_subscriptions import SOURCE
from app_dashboard.scope import Scope


def current_trials(
    conn: psycopg.Connection,
    scope: Scope = Scope.all(),
    *,
    now: datetime | None = None,
) -> dict:
    """Return active trials, their conversion value, and snapshot coverage."""
    now = now or datetime.now(timezone.utc)
    predicate, params = scope.predicate("current_sub")
    cursor = conn.cursor(row_factory=dict_row)
    rows = cursor.execute(
        f"""
        select current_sub.app_id, a.slug as app_slug, a.name as app_name,
               current_sub.shop_gid,
               coalesce(s.shop_name, s.shop_domain, current_sub.shop_gid) as shop,
               s.shop_domain, s.country,
               current_sub.legacy_subscription_id,
               current_sub.billing_period,
               current_sub.trial_ends_at,
               current_sub.cancel_at_end_of_cycle,
               current_sub.item_handle,
               current_sub.item_description,
               current_sub.currency_code,
               current_sub.observed_at,
               sub.monthly_amount
        from active_subscriptions current_sub
        join apps a on a.id = current_sub.app_id
        join shops s
          on s.app_id = current_sub.app_id
         and s.shop_gid = current_sub.shop_gid
        left join subscriptions sub
          on sub.app_id = current_sub.app_id
         and sub.id = current_sub.legacy_subscription_id
         and sub.shop_gid = current_sub.shop_gid
         and sub.churned_at is null
        where current_sub.trial_ends_at > %s
          and s.install_state = 'installed'
          and {predicate}
        order by current_sub.trial_ends_at, a.name, shop
        """,
        (now, *params),
    ).fetchall()

    for row in rows:
        seconds_left = (row["trial_ends_at"] - now).total_seconds()
        row["hours_left"] = max(1, ceil(seconds_left / 3600))
        row["days_left"] = max(1, ceil(seconds_left / 86400))

    app_predicate = "true" if scope.app_id is None else "a.id = %s"
    app_params = () if scope.app_id is None else (scope.app_id,)
    coverage = cursor.execute(
        f"""
        select count(*) as app_count,
               count(state.last_synced_at) as synced_apps,
               min(state.last_synced_at) as oldest_sync
        from apps a
        left join sync_state state
          on state.app_id = a.id and state.source = %s
        where a.active and {app_predicate}
        """,
        (SOURCE, *app_params),
    ).fetchone()

    known = [row["monthly_amount"] for row in rows if row["monthly_amount"] is not None]
    converting = [
        row["monthly_amount"] for row in rows
        if row["monthly_amount"] is not None and not row["cancel_at_end_of_cycle"]
    ]
    cancelling = [
        row["monthly_amount"] for row in rows
        if row["monthly_amount"] is not None and row["cancel_at_end_of_cycle"]
    ]
    return {
        "rows": rows,
        "count": len(rows),
        "ending_soon": sum(row["days_left"] <= 7 for row in rows),
        "cancel_scheduled": sum(row["cancel_at_end_of_cycle"] for row in rows),
        "potential_mrr": sum(known, Decimal("0")),
        "converting_mrr": sum(converting, Decimal("0")),
        "cancelling_mrr": sum(cancelling, Decimal("0")),
        "known_mrr_count": len(known),
        "app_count": coverage["app_count"],
        "synced_apps": coverage["synced_apps"],
        "oldest_sync": coverage["oldest_sync"],
        "sync_complete": bool(
            coverage["app_count"]
            and coverage["synced_apps"] == coverage["app_count"]
        ),
    }
