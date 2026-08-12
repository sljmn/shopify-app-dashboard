import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import httpx
from apscheduler.schedulers.background import BackgroundScheduler

from app_dashboard.active_subscriptions import sync_active_subscriptions
from app_dashboard.catalog import AppConfig
from app_dashboard.digest import send_weekly_digest
from app_dashboard.ops import check_stale_sync
from app_dashboard.partner_api import PartnerClient
from app_dashboard.pipeline import run_sync, sync_payout_earnings, sync_transactions

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


def _sync_one_payouts(conn_factory, client, app, settings) -> dict:
    conn = conn_factory()
    try:
        return sync_payout_earnings(conn, client, app, settings)
    finally:
        conn.close()


def run_payouts_job(conn_factory, apps: list[AppConfig], settings) -> list[dict]:
    results = run_all_apps(conn_factory, apps, settings, _sync_one_payouts)
    logger.info("all payout earning syncs completed: %s", results)
    return results


def _sync_one_active_subscriptions(
    conn_factory, client, app, settings, *, full_refresh: bool = True
) -> dict:
    conn = conn_factory()
    try:
        return sync_active_subscriptions(
            conn, client, app, full_refresh=full_refresh
        )
    finally:
        conn.close()


def run_active_subscriptions_job(
    conn_factory, apps: list[AppConfig], settings, *, full_refresh: bool = True
) -> list[dict]:
    def sync_one(conn_factory, client, app, settings):
        return _sync_one_active_subscriptions(
            conn_factory,
            client,
            app,
            settings,
            full_refresh=full_refresh,
        )

    results = run_all_apps(
        conn_factory, apps, settings, sync_one
    )
    logger.info("all active subscription syncs completed: %s", results)
    return results


def run_lifecycle_cycle(
    conn_factory, apps: list[AppConfig], settings
) -> dict[str, list[dict]]:
    """Ingest lifecycle events, then resolve current trial state immediately.

    Shopify's event feed contains the plan price but not ``trialEndsAt``. The
    incremental snapshot pass only revisits shops changed by the lifecycle
    sync, preventing a new trial from appearing as paid MRR until the separate
    six-hour full refresh runs.
    """
    lifecycle = run_sync_job(conn_factory, apps, settings)
    subscriptions = run_active_subscriptions_job(
        conn_factory, apps, settings, full_refresh=False
    )
    return {"lifecycle": lifecycle, "active_subscriptions": subscriptions}


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


def run_aso_job(conn_factory, apps: list[AppConfig], settings) -> list[dict]:
    """Refresh owned ASO sources per app without coupling their failures."""
    del settings
    from app_dashboard.aso_ga4 import (
        sync_aso_keywords,
        sync_capabilities,
        sync_install_sources,
    )
    from app_dashboard.ga4 import build_client

    results = []
    for app in apps:
        if not app.ga4_credentials_json or not app.ga4_property_id:
            continue
        conn = conn_factory()
        try:
            client = build_client(app.ga4_credentials_json)
            capability = sync_capabilities(conn, client, app)
            written = {"keywords": 0, "attribution": 0}
            if capability.statuses["aso_keywords"] in {"ready", "partial"}:
                written["keywords"] = sync_aso_keywords(
                    conn, client, app, fields=capability.fields
                )
            if "shop_domain" in capability.fields:
                written["attribution"] = sync_install_sources(
                    conn, client, app, fields=capability.fields
                )
            results.append({
                "app": app.slug, "ok": True, "written": written,
                "statuses": capability.statuses,
            })
        except Exception as exc:
            logger.exception("%s ASO sync failed", app.slug)
            results.append({"app": app.slug, "ok": False, "error": type(exc).__name__})
        finally:
            conn.close()
    return results


def run_listing_job(conn_factory, apps: list[AppConfig]) -> list[dict]:
    from app_dashboard.listing_intelligence import sync_listing

    results = []
    for app in apps:
        if not app.listing_url:
            continue
        conn = conn_factory()
        try:
            result = sync_listing(conn, app)
            results.append({"app": app.slug, "ok": result["status"] == "ready", **result})
        except Exception as exc:
            logger.exception("%s listing sync failed", app.slug)
            results.append({"app": app.slug, "ok": False, "error": type(exc).__name__})
        finally:
            conn.close()
    return results


def run_keyword_research_job(conn_factory) -> dict:
    from app_dashboard.listing_intelligence import research_seeds, sync_popular_keywords

    conn = conn_factory()
    try:
        written = sync_popular_keywords(conn, research_seeds(conn))
        return {"ok": True, "written": written}
    except Exception as exc:
        logger.exception("keyword research sync failed")
        return {"ok": False, "error": type(exc).__name__}
    finally:
        conn.close()


def run_app_discovery_job(conn_factory) -> dict:
    from app_dashboard.app_store_discovery import run_app_discovery

    conn = conn_factory()
    try:
        result = run_app_discovery(conn)
        logger.info("App Store discovery completed: %s", result)
        return {"ok": True, **result}
    except Exception as exc:
        logger.exception("App Store discovery failed")
        return {"ok": False, "error": type(exc).__name__}
    finally:
        conn.close()


def run_category_discovery_job(conn_factory) -> dict:
    from app_dashboard.app_store_discovery import run_category_discovery
    from app_dashboard.discovery_watchlist import (
        follow_automatic_candidates,
        queue_category_alerts,
    )

    conn = conn_factory()
    try:
        result = run_category_discovery(conn)
        result["watchlist"] = follow_automatic_candidates(conn)
        result["alerts_queued"] = queue_category_alerts(conn)
        logger.info("App Store category discovery completed: %s", result)
        return {"ok": True, **result}
    except Exception as exc:
        logger.exception("App Store category discovery failed")
        return {"ok": False, "error": type(exc).__name__}
    finally:
        conn.close()


def run_watchlist_job(conn_factory, settings) -> list[dict]:
    from app_dashboard.discovery_watchlist import active_watched_apps
    from app_dashboard.watchlist_collector import sync_followed_listing

    conn = conn_factory()
    try:
        watched = active_watched_apps(conn)
    finally:
        conn.close()

    def sync_one(item):
        discovered_app_id, handle = item
        worker_conn = conn_factory()
        try:
            return sync_followed_listing(
                worker_conn, discovered_app_id, handle,
                media_root=settings.watchlist_media_path,
            )
        except Exception as exc:
            logger.exception("watchlist sync failed for %s", handle)
            return {"handle": handle, "ok": False, "error": type(exc).__name__}
        finally:
            worker_conn.close()

    with ThreadPoolExecutor(max_workers=settings.watchlist_concurrency) as pool:
        results = list(pool.map(sync_one, watched))
    logger.info("watchlist sync completed: %s", results)
    return results


def run_review_collection_job(conn_factory, settings) -> list[dict]:
    from app_dashboard.review_collector import review_sync_targets, sync_app_reviews

    conn = conn_factory()
    try:
        targets = review_sync_targets(
            conn, limit=getattr(settings, "review_app_batch_size", 250),
        )
    finally:
        conn.close()

    def sync_one(item):
        discovered_app_id, handle = item
        worker_conn = conn_factory()
        try:
            return sync_app_reviews(
                worker_conn, discovered_app_id, handle,
                max_backfill_pages=getattr(
                    settings, "review_backfill_pages_per_run", 1,
                ),
            )
        except Exception as exc:
            logger.exception("review sync failed for %s", handle)
            return {"handle": handle, "ok": False, "error": type(exc).__name__}
        finally:
            worker_conn.close()

    with ThreadPoolExecutor(max_workers=settings.watchlist_concurrency) as pool:
        results = list(pool.map(sync_one, targets))
    logger.info("review sync completed: %s", results)
    return results


def run_icon_collection_job(conn_factory, settings) -> list[dict]:
    from app_dashboard.icon_collector import icon_sync_targets, sync_app_icon

    conn = conn_factory()
    try:
        targets = icon_sync_targets(
            conn, limit=getattr(settings, "icon_app_batch_size", 500),
        )
    finally:
        conn.close()

    def sync_one(item):
        discovered_app_id, handle, icon_url = item
        worker_conn = conn_factory()
        try:
            return sync_app_icon(
                worker_conn, discovered_app_id, handle, icon_url,
                media_root=settings.watchlist_media_path,
            )
        except Exception as exc:
            logger.exception("icon sync failed for %s", handle)
            return {"handle": handle, "ok": False, "error": type(exc).__name__}
        finally:
            worker_conn.close()

    with ThreadPoolExecutor(max_workers=settings.watchlist_concurrency) as pool:
        results = list(pool.map(sync_one, targets))
    logger.info("icon sync completed: %s icons", len(results))
    return results


def run_developer_catalog_job(conn_factory, settings) -> list[dict]:
    from app_dashboard.developer_catalog import (
        developers_due_for_refresh,
        sync_developer_catalog,
    )

    conn = conn_factory()
    try:
        developer_ids = developers_due_for_refresh(conn)
    finally:
        conn.close()

    def sync_one(developer_id):
        worker_conn = conn_factory()
        try:
            return sync_developer_catalog(worker_conn, developer_id)
        except Exception as exc:
            logger.exception("developer catalog sync failed for %s", developer_id)
            return {"developer_id": developer_id, "status": "failed",
                    "error": type(exc).__name__}
        finally:
            worker_conn.close()

    with ThreadPoolExecutor(max_workers=settings.watchlist_concurrency) as pool:
        results = list(pool.map(sync_one, developer_ids))
    logger.info("developer catalog sync completed: %s", results)
    return results


def run_discovery_alerts_job(conn_factory, settings) -> dict:
    from app_dashboard.discovery_watchlist import deliver_discovery_alerts

    conn = conn_factory()
    try:
        return deliver_discovery_alerts(
            conn, settings.slack_webhook_url, settings.public_base_url
        )
    except Exception as exc:
        logger.exception("discovery alert delivery failed")
        return {"pending": 0, "delivered": 0, "error": type(exc).__name__}
    finally:
        conn.close()


def run_rank_tracker_job(conn_factory) -> list[dict]:
    """Measure active keywords independently so one search cannot stop a list."""
    from app_dashboard.rank_collector import sync_keyword_rankings

    conn = conn_factory()
    try:
        keyword_ids = [
            row[0] for row in conn.execute(
                """select k.id from aso_rank_keywords k
                   join aso_rank_lists l on l.id=k.rank_list_id
                   where k.active and l.status='active' order by k.id"""
            ).fetchall()
        ]
        results = []
        for keyword_id in keyword_ids:
            try:
                result = sync_keyword_rankings(conn, keyword_id)
                results.append({"keyword_id": keyword_id, **result})
            except Exception as exc:
                logger.exception("rank keyword %s failed", keyword_id)
                results.append({
                    "keyword_id": keyword_id,
                    "status": "failed",
                    "error": type(exc).__name__,
                })
        return results
    finally:
        conn.close()


def start_scheduler(conn_factory, settings, apps) -> BackgroundScheduler:
    """Poll the Partner API on an interval via run_sync. Caller owns shutdown()."""
    scheduler = BackgroundScheduler()
    current_apps = apps if callable(apps) else lambda: apps
    scheduler.add_job(
        lambda: run_lifecycle_cycle(conn_factory, current_apps(), settings),
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
        lambda: run_transactions_job(conn_factory, current_apps(), settings),
        "interval",
        hours=1,
        # Lifecycle also runs at boot. Keeping the Partner jobs apart avoids a
        # burst against organizations that own many apps.
        next_run_time=datetime.now() + timedelta(minutes=2),
        id="transactions",
    )
    scheduler.add_job(
        lambda: run_payouts_job(conn_factory, current_apps(), settings),
        "interval",
        hours=1,
        next_run_time=datetime.now() + timedelta(minutes=3),
        id="payout_earnings",
    )
    # Shopify exposes trial and scheduled-cancellation state only through a
    # per-shop query. Refresh independently so hundreds of calls never delay
    # the 15-minute lifecycle feed.
    scheduler.add_job(
        lambda: run_active_subscriptions_job(conn_factory, current_apps(), settings),
        "interval",
        hours=6,
        next_run_time=datetime.now() + timedelta(minutes=5),
        id="active_subscriptions",
    )
    # GA4 aggregates move slowly and the API has a daily token quota, so hourly
    # is plenty; the first run still happens at boot.
    scheduler.add_job(
        lambda: run_ga4_job(conn_factory, current_apps(), settings),
        "interval",
        hours=1,
        next_run_time=datetime.now(),
    )
    scheduler.add_job(
        lambda: run_aso_job(conn_factory, current_apps(), settings),
        "interval",
        hours=24,
        next_run_time=datetime.now() + timedelta(minutes=10),
        id="aso_intelligence",
    )
    scheduler.add_job(
        lambda: run_listing_job(conn_factory, current_apps()),
        "interval", hours=24,
        next_run_time=datetime.now() + timedelta(minutes=20),
        id="aso_listings",
    )
    scheduler.add_job(
        lambda: run_keyword_research_job(conn_factory),
        "interval", hours=24,
        next_run_time=datetime.now() + timedelta(minutes=30),
        id="aso_keyword_research",
    )
    # The sitemap is one cheap request for the complete public app registry.
    # Run shortly after boot so a new deployment establishes its baseline, then
    # at a stable local time every day.
    scheduler.add_job(
        lambda: run_app_discovery_job(conn_factory),
        "cron", hour=3, minute=30, timezone="Europe/Amsterdam",
        next_run_time=datetime.now() + timedelta(minutes=35),
        id="app_store_discovery",
    )
    # Category pages require many polite paginated requests. Keep this separate
    # from the daily sitemap and lifecycle jobs; a partial crawl never commits.
    scheduler.add_job(
        lambda: run_category_discovery_job(conn_factory),
        "cron", day_of_week="tue,fri", hour=4, minute=0,
        timezone="Europe/Amsterdam",
        next_run_time=datetime.now() + timedelta(minutes=45),
        id="app_store_categories",
    )
    scheduler.add_job(
        lambda: run_watchlist_job(conn_factory, settings),
        "interval", hours=24,
        next_run_time=datetime.now() + timedelta(minutes=60),
        id="watchlist_listings",
    )
    scheduler.add_job(
        lambda: run_review_collection_job(conn_factory, settings),
        "interval", hours=1,
        next_run_time=datetime.now() + timedelta(minutes=75),
        id="watchlist_reviews",
    )
    scheduler.add_job(
        lambda: run_icon_collection_job(conn_factory, settings),
        "interval", hours=1,
        next_run_time=datetime.now() + timedelta(minutes=80),
        id="app_store_icons",
    )
    scheduler.add_job(
        lambda: run_developer_catalog_job(conn_factory, settings),
        "cron", hour=5, minute=45, timezone="Europe/Amsterdam",
        next_run_time=datetime.now() + timedelta(minutes=90),
        id="research_developers",
    )
    scheduler.add_job(
        lambda: run_discovery_alerts_job(conn_factory, settings),
        "interval", minutes=15,
        next_run_time=datetime.now() + timedelta(minutes=65),
        id="discovery_alerts",
    )
    scheduler.add_job(
        lambda: run_rank_tracker_job(conn_factory),
        "cron", hour=6, minute=15, timezone="Europe/Amsterdam",
        next_run_time=datetime.now() + timedelta(minutes=105),
        id="aso_rank_tracker",
    )
    # Every 15 minutes, but it only posts once per stale episode. Deliberately
    # a separate job from run_sync: a check that lives inside the thing it is
    # watching never runs when that thing is the failure.
    scheduler.add_job(
        lambda: run_stale_check_job(conn_factory, current_apps(), settings),
        "interval",
        minutes=15,
    )
    # DIGEST_DAY_OF_WEEK at DIGEST_HOUR in DIGEST_TIMEZONE. A cron trigger, not
    # an interval, so it lands at the same local time year round;
    # send_weekly_digest itself refuses to post twice in one week, which is what
    # makes a machine restart on digest morning harmless.
    scheduler.add_job(
        lambda: run_digest_job(conn_factory, current_apps(), settings),
        "cron",
        day_of_week=settings.digest_day_of_week,
        hour=settings.digest_hour,
        minute=0,
        timezone=settings.digest_timezone,
        id=WEEKLY_DIGEST_JOB_ID,
    )
    scheduler.start()
    return scheduler
