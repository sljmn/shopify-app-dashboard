import time
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from app_dashboard.catalog import AppConfig
from app_dashboard.normalize import normalize_monthly
from app_dashboard.partner_api import fetch_active_subscription

SOURCE = "partner_active_subscriptions"
THROTTLE_SECONDS = 0.3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_future(value: str | None, observed_at: datetime) -> bool:
    if not value:
        return False
    return datetime.fromisoformat(value.replace("Z", "+00:00")) > observed_at


def _reconcile_paid_state(
    conn: psycopg.Connection, app_id: int, shop_gid: str, snapshot: dict
) -> bool:
    """Correct event-derived current MRR with Shopify's current paid state.

    Shopify can keep the legacy subscription ID while a plan change event uses
    a newly minted ID. The snapshot has the authoritative ID and interval but
    exposes no amount, so the latest positive subscription sale supplies the
    price. Historical raw events remain untouched; only their clean movement
    and the current derived subscription are corrected.
    """
    snapshot_id = snapshot.get("legacy_subscription_id")
    if not snapshot_id:
        return False
    payment = conn.execute(
        """
        select gross_amount, coalesce(billing_interval, %s)
        from transactions
        where app_id = %s and shop_gid = %s and charge_gid = %s
          and type = 'AppSubscriptionSale' and gross_amount > 0
        order by created_at desc, id desc
        limit 1
        """,
        (snapshot.get("billing_period"), app_id, shop_gid, snapshot_id),
    ).fetchone()
    if payment is None or payment[1] is None:
        return False
    target = normalize_monthly(payment[0], payment[1])
    live = conn.execute(
        """
        select id, monthly_amount, converted_at
        from subscriptions
        where app_id = %s and shop_gid = %s and churned_at is null
        order by converted_at desc nulls last, id
        limit 1
        """,
        (app_id, shop_gid),
    ).fetchone()
    if live is None:
        return False
    live_id, current, converted_at = live
    if live_id == snapshot_id:
        return False
    current = current or 0
    difference = target - current
    conn.execute(
        """
        update subscriptions
        set monthly_amount = %s, billing_type = %s
        where app_id = %s and id = %s
        """,
        (target, payment[1], app_id, live_id),
    )
    if difference and converted_at is not None:
        movement = conn.execute(
            """
            select id, type, net_change
            from app_events
            where app_id = %s and shop_gid = %s and occurred_at = %s
              and type in ('subscribed', 'resubscribed', 'upgraded', 'downgraded',
                           'subscription_reconciled')
            order by id desc limit 1
            """,
            (app_id, shop_gid, converted_at),
        ).fetchone()
        if movement is not None:
            event_id, event_type, net_change = movement
            corrected = (net_change or 0) + difference
            if event_type in {"upgraded", "downgraded", "subscription_reconciled"}:
                if corrected > 0:
                    event_type = "upgraded"
                elif corrected < 0:
                    event_type = "downgraded"
                else:
                    event_type = "subscription_reconciled"
            conn.execute(
                "update app_events set net_change = %s, type = %s where id = %s",
                (corrected, event_type, event_id),
            )
    return bool(difference or live_id != snapshot_id)


def sync_active_subscriptions(
    conn: psycopg.Connection,
    client,
    app: AppConfig,
    *,
    sleep=time.sleep,
    now=_utcnow,
) -> dict:
    """Refresh Shopify's current subscription snapshot for installed shops."""
    observed_at = now()
    shop_ids = [
        row[0]
        for row in conn.execute(
            """select shop_gid from shops
               where app_id=%s and install_state='installed'
               order by shop_gid""",
            (app.id,),
        ).fetchall()
    ]
    stored = removed = trials = reconciled = 0
    for index, shop_gid in enumerate(shop_ids):
        snapshot = fetch_active_subscription(
            client, app_id=app.partner_app_id, shop_id=shop_gid
        )
        if snapshot is None:
            removed += conn.execute(
                "delete from active_subscriptions where app_id=%s and shop_gid=%s",
                (app.id, shop_gid),
            ).rowcount
        else:
            conn.execute(
                """
                insert into active_subscriptions (
                    app_id, shop_gid, legacy_subscription_id, billing_period,
                    trial_ends_at, cancel_at_end_of_cycle, item_handle,
                    item_description, currency_code, payload, observed_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (app_id, shop_gid) do update set
                    legacy_subscription_id=excluded.legacy_subscription_id,
                    billing_period=excluded.billing_period,
                    trial_ends_at=excluded.trial_ends_at,
                    cancel_at_end_of_cycle=excluded.cancel_at_end_of_cycle,
                    item_handle=excluded.item_handle,
                    item_description=excluded.item_description,
                    currency_code=excluded.currency_code,
                    payload=excluded.payload,
                    observed_at=excluded.observed_at
                """,
                (
                    app.id,
                    shop_gid,
                    snapshot["legacy_subscription_id"],
                    snapshot["billing_period"],
                    snapshot["trial_ends_at"],
                    snapshot["cancel_at_end_of_cycle"],
                    snapshot["item_handle"],
                    snapshot["item_description"],
                    snapshot["currency_code"],
                    Jsonb(snapshot["payload"]),
                    observed_at,
                ),
            )
            stored += 1
            trials += int(_is_future(snapshot["trial_ends_at"], observed_at))
            if not _is_future(snapshot["trial_ends_at"], observed_at):
                reconciled += int(
                    _reconcile_paid_state(conn, app.id, shop_gid, snapshot)
                )
        if index < len(shop_ids) - 1:
            sleep(THROTTLE_SECONDS)

    conn.execute(
        """
        insert into sync_state (app_id, source, cursor, last_synced_at)
        values (%s, %s, null, %s)
        on conflict (app_id, source) do update set
            cursor=null, last_synced_at=excluded.last_synced_at
        """,
        (app.id, SOURCE, observed_at),
    )
    conn.commit()
    return {
        "app": app.slug,
        "ok": True,
        "queried": len(shop_ids),
        "stored": stored,
        "removed": removed,
        "trials": trials,
        "reconciled": reconciled,
    }
