import time
from datetime import datetime, timedelta, timezone

import psycopg

from app_dashboard.catalog import AppConfig
from app_dashboard.derive import derive_installations
from app_dashboard.ingest_raw import upsert_charges, upsert_raw_events, upsert_transactions
from app_dashboard.partner_api import fetch_app_events, fetch_transactions
from app_dashboard.slack import notify_events

SOURCE = "partner_api"
TRANSACTIONS_SOURCE = "partner_transactions"

# The Partner API rate-limits hard enough to 429 an introspection burst, and the
# transactions feed is the only place we page in a tight loop. Same 0.3s the
# forsbergplustwo/partner-metrics importer settled on.
THROTTLE_SECONDS = 0.3


def run_sync(
    conn: psycopg.Connection, client, app: AppConfig, settings, http_post,
    *, full_history: bool = False,
) -> dict:
    """Poll the Partner API, derive clean events, and notify fresh installs.

    The feed is newest-first. Its cursor is valid only while paging toward older
    events inside this run; persisting it would make the next run resume below
    newly-added events. Cross-run progress therefore uses an overlapping time
    boundary and every run starts with a null cursor.
    """
    start_ts = datetime.now(timezone.utc)

    row = conn.execute(
        "select last_synced_at from sync_state where app_id = %s and source = %s",
        (app.id, SOURCE),
    ).fetchone()
    last_synced_at = row[0] if row else None
    occurred_at_min = None
    if last_synced_at is not None and not full_history:
        occurred_at_min = (
            last_synced_at - timedelta(minutes=settings.poll_overlap_minutes)
        ).astimezone(timezone.utc).isoformat()
    cursor = None

    # Snapshot before deriving: ON CONFLICT (platform_event_id) DO NOTHING
    # means already-seen events never get a new id, so any app_events row with
    # id > snapshot after this run is a genuinely new lifecycle event.
    (snapshot,) = conn.execute(
        "select coalesce(max(id), 0) from app_events where app_id = %s", (app.id,)
    ).fetchone()

    raw_inserted = 0
    touched_ids: set[str] = set()
    while True:
        events, next_cursor = fetch_app_events(
            client,
            app_id=app.partner_app_id,
            after_cursor=cursor,
            occurred_at_min=occurred_at_min,
        )
        raw_inserted += upsert_raw_events(conn, app, events)
        upsert_charges(conn, app, events)
        touched_ids.update(e["shop_gid"] for e in events)
        if next_cursor is None:
            break
        cursor = next_cursor

    # Derive exactly the installs touched by this poll, not derive_all_dirty's
    # since=start_ts: that compares an app-clock timestamp against DB-clock
    # ingested_at, so with the app and database on separate machines clock skew can
    # permanently skip a freshly-ingested install.
    event_counts = derive_installations(conn, app.id, touched_ids)

    alertable = conn.execute(
        """
        select shop_gid, type from app_events
        where app_id = %s and id > %s
          and type in ('installed', 'reinstalled', 'uninstalled')
        order by id
        """,
        (app.id, snapshot),
    ).fetchall()
    alerts_sent = notify_events(conn, app, alertable, settings.slack_webhook_url,
                                http_post=http_post,
                                base_url=settings.public_base_url)

    conn.execute(
        """
        insert into sync_state (app_id, source, cursor, last_synced_at)
        values (%s, %s, %s, %s)
        on conflict (app_id, source) do update set
            cursor = null,
            last_synced_at = excluded.last_synced_at
        """,
        (app.id, SOURCE, None, start_ts),
    )
    conn.commit()

    return {
        "app": app.slug,
        "ok": True,
        "raw_inserted": raw_inserted,
        "events_emitted": sum(event_counts.values()),
        "alerts_sent": alerts_sent,
    }


def sync_transactions(
    conn: psycopg.Connection,
    client,
    app: AppConfig,
    settings,
    sleep=time.sleep,
    *,
    full_history: bool = False,
) -> dict:
    """Poll the money feed into `transactions`.

    Its own sync_state row, keyed apart from lifecycle progress, so replaying
    payments never forces an event replay (and a broken money sync never stalls
    the lifecycle one).

    No stored cursor. `transactions` accepts createdAtMin, so the window is
    derived from the data itself -- the newest transaction we hold, rewound by
    poll_overlap_minutes. That is what makes the overlap idea work here at all:
    on the events feed the cursor is an opaque token with no time semantics and
    nothing to rewind (see run_sync). An empty table means no bound, i.e. pull
    the whole history.
    """
    start_ts = datetime.now(timezone.utc)

    (latest,) = conn.execute(
        "select max(created_at) from transactions where app_id = %s", (app.id,)
    ).fetchone()
    created_at_min = None
    if latest is not None and not full_history:
        # Normalized to UTC before formatting: psycopg hands back timestamptz in
        # the session's timezone, so an un-normalized isoformat would send
        # Shopify a local offset that reads correctly but is needlessly
        # ambiguous next to every other timestamp in this codebase.
        created_at_min = (
            latest - timedelta(minutes=settings.poll_overlap_minutes)
        ).astimezone(timezone.utc).isoformat()

    cursor = None
    inserted = seen = pages = 0
    while True:
        rows, next_cursor = fetch_transactions(
            client, app_id=app.partner_app_id, after_cursor=cursor,
            created_at_min=created_at_min,
        )
        inserted += upsert_transactions(conn, app, rows)
        seen += len(rows)
        pages += 1
        if next_cursor is None:
            break
        cursor = next_cursor
        sleep(THROTTLE_SECONDS)

    conn.execute(
        """
        insert into sync_state (app_id, source, cursor, last_synced_at)
        values (%s, %s, null, %s)
        on conflict (app_id, source) do update set last_synced_at = excluded.last_synced_at
        """,
        (app.id, TRANSACTIONS_SOURCE, start_ts),
    )
    conn.commit()

    return {"app": app.slug, "ok": True,
            "transactions_seen": seen, "transactions_inserted": inserted,
            "pages": pages, "since": created_at_min}
