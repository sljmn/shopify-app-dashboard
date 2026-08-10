import time
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from app_dashboard.catalog import AppConfig
from app_dashboard.partner_api import fetch_active_subscription

SOURCE = "partner_active_subscriptions"
THROTTLE_SECONDS = 0.3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_future(value: str | None, observed_at: datetime) -> bool:
    if not value:
        return False
    return datetime.fromisoformat(value.replace("Z", "+00:00")) > observed_at


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
    stored = removed = trials = 0
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
    }
