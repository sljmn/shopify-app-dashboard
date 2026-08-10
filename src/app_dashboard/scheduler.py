import logging
from datetime import datetime, timedelta

import httpx
from apscheduler.schedulers.background import BackgroundScheduler

from app_dashboard.catalog import AppConfig
from app_dashboard.active_subscriptions import sync_active_subscriptions
from app_dashboard.digest import send_weekly_digest
from app_dashboard.ops import check_stale_sync
from app_dashboard.partner_api import PartnerClient
from app_dashboard.pipeline import run_sync, sync_transactions

logger = logging.getLogger(__name__)

WEEKLY_DIGEST_JOB_ID = "weekly_digest"


def configured_clients(apps: list[AppConfig]) -> dict[str, PartnerClient]:
    """Build one API client per Partner organization, shared by its apps."""
    clients: dict[str, PartnerClient] = {}
    for app in apps:
        if app.partner_org_id not in clients:
            clients[app.partner_org_id] = PartnerClient(
                app.partner_token, app.partner_org_id
            )
    return clients


def run_all_apps(conn_factory, apps, settings, sync_one) -> list[dict]:
    """Run every app independently so one Partner failure cannot stop the rest."""
    results = []
    clients = configured_clients(apps)
    for app in apps:
        try:
            results.append(
                sync_one(conn_factory, clients[app.partner_org_id], app, settings)
            )
        except Exception as exc:
            logger.exception("%s sync failed", app.slug)
            results.append({"app": app.slug, "ok": False, "error": str(exc)})
    return results


def _sync_one_lifecycle(conn_factory, client, app, settings) -> dict:
    conn = conn_factory()
    try:
        return run_sync(conn, client, app, settings, http_post=httpx.post)
    finally:
        conn.close()


def run_sync_job(conn_factory, apps: list[AppConfig], settings) -> list[dict]:
    results = run_all_apps(conn_factory, apps, settings, _sync_one_lifecycle)
    logger.info("all lifecycle syncs completed: %s", results)
    return results


def _sync_one_transactions(conn_factory, client, app, settings) -> dict:
    conn = conn_factory()
    try:
        return sync_transactions(conn, client, app, settings)
    finally:
        conn.close()


def run_transactions_job(conn_factory, apps: list[AppConfig], settings) -> list[dict]:
    """Poll the money feed. Its own job, and its own try/except: a failure here
    must not take the lifecycle sync down, because the events feed is what the
    install/uninstall alerts run on."""
    results = run_all_apps(conn_factory, apps, settings, _sync_one_transactions)
    logger.info("all transaction syncs completed: %s", results)
    return results


def _sync_one_active_subscriptions(conn_factory, client, app, settings) -> dict:
    conn = conn_factory()
    try:
        return sync_active_subscriptions(conn, client, app)
    finally:
        conn.close()


def run_active_subscriptions_job(
    conn_factory, apps: list[AppConfig], settings
) -> list[dict]:
    results = run_all_apps(
        conn_factory, apps, settings, _sync_one_active_subscriptions
    )
    logger.info("all active subscription syncs completed: %s", results)
    return results


def run_stale_check_job(conn_factory, apps: list[AppConfig], settings) -> None:
    """Shout in Slack if the Partner API sync has stopped. Runs on its own job:
    if run_sync is the thing that is broken, a check inside it never fires."""
    conn = conn_factory()
    try:
        for app in apps:
            check_stale_sync(conn, app, settings, http_post=httpx.post)
    except Exception:
        logger.exception("stale-sync check failed")
    finally:
        conn.close()


def run_digest_job(conn_factory, apps: list[AppConfig], settings) -> None:
    conn = conn_factory()
    try:
        if send_weekly_digest(conn, apps, settings, http_post=httpx.post):
            logger.info("posted weekly digest")
    except Exception:
        logger.exception("weekly digest failed")
    finally:
        conn.close()


def run_ga4_job(conn_factory, apps: list[AppConfig], settings) -> list[dict]:
    """Refresh listing traffic independently for every configured GA4 app."""
    from app_dashboard.ga4 import build_client, sync_ga4

    results = []
    for app in apps:
        if not app.ga4_credentials_json or not app.ga4_property_id:
            continue
        conn = conn_factory()
        try:
            client = build_client(app.ga4_credentials_json)
            written = sync_ga4(conn, client, app)
            results.append({"app": app.slug, "ok": True, "written": written})
        except Exception as exc:
            logger.exception("%s GA4 sync failed", app.slug)
            results.append({"app": app.slug, "ok": False, "error": str(exc)})
        finally:
            conn.close()
    if not results:
        logger.info("no apps have GA4 credentials -- skipping traffic sync")
    return results


def start_scheduler(
    conn_factory, settings, apps: list[AppConfig]
) -> BackgroundScheduler:
    """Poll the Partner API on an interval via run_sync. Caller owns shutdown()."""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: run_sync_job(conn_factory, apps, settings),
        "interval",
        minutes=settings.poll_interval_minutes,
        # First run at boot, not boot+interval: a fresh deploy should sync
        # immediately (the very first ever run replays full app history).
        next_run_time=datetime.now(),
    )
    # Money settles on Shopify's schedule, not ours: a charge is created, then
    # collected some hours later. Hourly is well inside that, and it keeps the
    # tight pagination loop away from the 15-minute lifecycle poll.
    scheduler.add_job(
        lambda: run_transactions_job(conn_factory, apps, settings),
        "interval",
        hours=1,
        next_run_time=datetime.now(),
    )
    # Shopify exposes trial and scheduled-cancellation state only through a
    # per-shop query. Refresh independently so hundreds of calls never delay
    # the 15-minute lifecycle feed.
    scheduler.add_job(
        lambda: run_active_subscriptions_job(conn_factory, apps, settings),
        "interval",
        hours=6,
        next_run_time=datetime.now() + timedelta(minutes=5),
        id="active_subscriptions",
    )
    # GA4 aggregates move slowly and the API has a daily token quota, so hourly
    # is plenty; the first run still happens at boot.
    scheduler.add_job(
        lambda: run_ga4_job(conn_factory, apps, settings),
        "interval",
        hours=1,
        next_run_time=datetime.now(),
    )
    # Every 15 minutes, but it only posts once per stale episode. Deliberately
    # a separate job from run_sync: a check that lives inside the thing it is
    # watching never runs when that thing is the failure.
    scheduler.add_job(
        lambda: run_stale_check_job(conn_factory, apps, settings),
        "interval",
        minutes=15,
    )
    # DIGEST_DAY_OF_WEEK at DIGEST_HOUR in DIGEST_TIMEZONE. A cron trigger, not
    # an interval, so it lands at the same local time year round;
    # send_weekly_digest itself refuses to post twice in one week, which is what
    # makes a machine restart on digest morning harmless.
    scheduler.add_job(
        lambda: run_digest_job(conn_factory, apps, settings),
        "cron",
        day_of_week=settings.digest_day_of_week,
        hour=settings.digest_hour,
        minute=0,
        timezone=settings.digest_timezone,
        id=WEEKLY_DIGEST_JOB_ID,
    )
    scheduler.start()
    return scheduler
