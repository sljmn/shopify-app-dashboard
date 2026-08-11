#!/usr/bin/env python3
"""Run the data invariants against a live database. Read-only.

`tests/test_invariants.py` asserts these against a seeded fixture on every
pytest run. This runs the same questions against real data, which the fixture
cannot do: a live app carries years of feed quirks nobody thought to seed.

Usage, against whatever database you point it at:

    DATABASE_URL='postgres://...' uv run python scripts/check_invariants.py

If the database is not directly reachable, tunnel to it and point DATABASE_URL
at the local end of the tunnel.

Exits non-zero if any invariant fails, so it can gate a deploy.
"""

import os
import sys
from decimal import Decimal

import psycopg

from app_dashboard import stats
from app_dashboard.scope import Scope

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "", scope: int | None = None) -> None:
    """Report one invariant.

    `scope` is how many rows the check actually examined. A check that finds no
    violations because it had nothing to look at is not evidence of anything,
    and printing it as a bare PASS is how a misconfigured deployment gets a
    clean bill of health. The annual-plan check is the live example: with
    ANNUAL_PLAN_AMOUNTS unset, nothing is labelled ANNUAL, so it inspects zero
    rows and passes while every annual subscriber is counted at 12x.
    """
    label = "PASS" if ok else "FAIL"
    suffix = ""
    if scope is not None:
        suffix = f"  ({scope} rows in scope)" if scope else "  (0 rows in scope -- proves nothing)"
    print(f"{label}  {name}{suffix}")
    if not ok:
        if detail:
            print(f"      {detail}")
        FAILURES.append(name)


def rows(conn, sql):
    return conn.execute(sql).fetchall()


def check_app_metrics(conn, app_id: int, slug: str) -> None:
    scope = Scope.for_app(app_id)
    prefix = f"{slug}: "
    tile = stats.overview_stats(conn, scope)["active_mrr"]
    chart = stats.mrr_trend(conn, scope=scope)[-1]["mrr"]
    mix = sum((p["mrr"] for p in stats.plan_mix(conn, scope)), Decimal("0"))
    check(prefix + "Active MRR tile == last MRR chart bucket", tile == chart,
          f"tile {tile}, chart {chart}")
    check(prefix + "Active MRR tile == sum of plan mix", tile == mix,
          f"tile {tile}, mix {mix}")
    ledger = stats.current_event_mrr(conn, scope)
    check(prefix + "Active MRR state == clean event ledger", tile == ledger,
          f"state {tile}, ledger {ledger}")

    trend = stats.mrr_trend(conn, scope=scope)
    movements = stats.mrr_movements(conn, scope=scope)
    bad = []
    for i, movement in enumerate(movements):
        parts = sum(movement[k] for k in stats.MOVEMENT_KINDS)
        if parts != movement["net"]:
            bad.append(f"{movement['label']}: buckets {parts} != net {movement['net']}")
        if i and parts != trend[i]["mrr"] - trend[i - 1]["mrr"]:
            bad.append(f"{movement['label']}: waterfall differs from trend")
    check(prefix + "Movement buckets decompose the trend", not bad, "; ".join(bad))

    summary = stats.overview_stats(conn, scope)
    funnel = next(
        row for row in stats.funnel_stats(conn, scope)
        if row["label"] == "Currently paying"
    )
    check(
        prefix + "Paying count agrees across every path",
        summary["paying"] == stats.unit_economics(conn, scope=scope)["paying"]
        == funnel["count"],
    )


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    # Same TimeZone pin as app_dashboard.db.connect: month buckets resolve in the
    # session's timezone, so checking under a different one would compare
    # different months to the ones the dashboard renders.
    conn = psycopg.connect(url, autocommit=True, options="-c TimeZone=UTC")

    for app_id, slug in conn.execute(
        "select id, slug from apps where active order by slug"
    ).fetchall():
        check_app_metrics(conn, app_id, slug)

    check("No shop has two simultaneously-live subscriptions",
          not rows(conn, """select app_id, shop_gid from subscriptions
                            where churned_at is null group by app_id, shop_gid
                            having count(*) > 1"""))

    check("No uninstalled shop has a live subscription",
          not rows(conn, """select sub.id from subscriptions sub
                            join shops s on s.app_id = sub.app_id
                                        and s.shop_gid = sub.shop_gid
                            where sub.churned_at is null and s.install_state <> 'installed'"""))

    check("No subscription churns before it converts",
          not rows(conn, """select id from subscriptions
                            where churned_at is not null and churned_at < converted_at"""))

    check("Every subscription has a conversion event",
          not rows(conn, "select id from subscriptions where converted_at is null"))

    check("install_state matches each shop's last lifecycle event",
          not rows(conn, """
            select s.shop_gid from shops s
            join lateral (
                select type from app_events e
                where e.app_id = s.app_id and e.shop_gid = s.shop_gid
                  and e.type in ('installed', 'reinstalled', 'uninstalled')
                order by e.occurred_at desc, e.id desc limit 1
            ) last on true
            where s.install_state <> case when last.type = 'uninstalled'
                                          then 'uninstalled' else 'installed' end"""))

    check("No test charge contributes to any figure",
          not rows(conn, """select sub.id from subscriptions sub
                            join charges c on c.app_id = sub.app_id and c.gid = sub.id
                            where c.test"""))

    # Scope is reported because this check is silently disarmed by the exact
    # misconfiguration it exists to catch. ANNUAL_PLAN_AMOUNTS is what labels a
    # charge ANNUAL; leave it empty and there are no annual rows to disagree
    # with, so this passes over nothing while MRR reads twelve times high.
    annual_scope = len(rows(conn, """select sub.id from subscriptions sub
                                     join charges c on c.app_id = sub.app_id and c.gid = sub.id
                                     where c.plan_interval = 'ANNUAL'"""))
    check("Every annual subscription counts at one twelfth of its price",
          not rows(conn, """select sub.id from subscriptions sub
                            join charges c on c.app_id = sub.app_id and c.gid = sub.id
                            where c.plan_interval = 'ANNUAL'
                              and sub.monthly_amount
                                  <> round(coalesce(c.plan_amount, c.amount) / 12, 2)"""),
          detail="", scope=annual_scope)

    money = stats.collected_revenue(conn)
    check("Collected revenue: gross - taken == net",
          money["gross"] - money["taken"] == money["net"])

    check("No orphaned shop_gid in subscriptions or app_events",
          not rows(conn, """
            select 'subscriptions' from subscriptions t
             where not exists (select 1 from shops s
                               where s.app_id = t.app_id and s.shop_gid = t.shop_gid)
            union all
            select 'app_events' from app_events t
             where not exists (select 1 from shops s
                               where s.app_id = t.app_id and s.shop_gid = t.shop_gid)"""))

    check("Every app_event traces back to a raw event",
          not rows(conn, """select e.id from app_events e where not exists
                            (select 1 from raw_app_events r
                             where r.app_id = e.app_id and r.id = e.platform_event_id)"""))

    snapshot_mismatches = rows(conn, """
        select a.slug, coalesce(s.shop_name, s.shop_domain, s.shop_gid),
               sub.monthly_amount,
               round(coalesce(latest_payment.gross_amount,
                              snapshot_charge.plan_amount, snapshot_charge.amount,
                              live_charge.plan_amount, live_charge.amount) /
                   case when current_sub.billing_period = 'ANNUAL'
                        then 12 else 1 end, 2)
        from subscriptions sub
        join apps a on a.id = sub.app_id
        join shops s on s.app_id = sub.app_id and s.shop_gid = sub.shop_gid
        join active_subscriptions current_sub
          on current_sub.app_id = sub.app_id and current_sub.shop_gid = sub.shop_gid
        left join lateral (
            select t.gross_amount
            from transactions t
            where t.app_id = sub.app_id and t.shop_gid = sub.shop_gid
              and t.charge_gid = current_sub.legacy_subscription_id
              and t.type = 'AppSubscriptionSale' and t.gross_amount > 0
            order by t.created_at desc, t.id desc limit 1
        ) latest_payment on true
        left join charges snapshot_charge
          on snapshot_charge.app_id = sub.app_id
         and snapshot_charge.gid = current_sub.legacy_subscription_id
         and not snapshot_charge.test
        left join charges live_charge
          on live_charge.app_id = sub.app_id and live_charge.gid = sub.id
         and not live_charge.test
        where sub.churned_at is null and sub.monthly_amount > 0.01
          and s.install_state = 'installed'
          and current_sub.legacy_subscription_id is not null
          and current_sub.billing_period is not null
          and not exists (
              select 1 from active_subscriptions trial
              where trial.app_id = sub.app_id and trial.shop_gid = sub.shop_gid
                and trial.trial_ends_at > now()
          )
          and coalesce(latest_payment.gross_amount,
                       snapshot_charge.plan_amount, snapshot_charge.amount,
                       live_charge.plan_amount, live_charge.amount) is not null
          and (sub.monthly_amount <> round(
                   coalesce(latest_payment.gross_amount,
                            snapshot_charge.plan_amount, snapshot_charge.amount,
                            live_charge.plan_amount, live_charge.amount) /
                   case when current_sub.billing_period = 'ANNUAL'
                        then 12 else 1 end, 2)
               or coalesce(sub.billing_type, '')
                    <> current_sub.billing_period)
          and exists (
              select 1 from sync_state state
              where state.app_id = sub.app_id
                and state.source = 'partner_active_subscriptions'
          )
    """)
    check("Shopify snapshots reconcile to current paid pricing",
          not snapshot_mismatches,
          f"{len(snapshot_mismatches)} paid subscription(s) disagree")

    missing_clean = rows(conn, """
        select r.type, count(*)
        from raw_app_events r
        join charges c on c.app_id = r.app_id and c.gid = r.charge_gid and not c.test
        left join app_events e on e.app_id = r.app_id and e.platform_event_id = r.id
        where (r.type = 'SUBSCRIPTION_CHARGE_UNFROZEN'
               and e.type is distinct from 'subscription_unfrozen')
           or (r.type = 'SUBSCRIPTION_CHARGE_FROZEN'
               and e.type is distinct from 'subscription_frozen')
           or (r.type in ('SUBSCRIPTION_CHARGE_DECLINED', 'SUBSCRIPTION_CHARGE_EXPIRED')
               and e.type is distinct from 'charge_abandoned')
        group by r.type
    """)
    check("Supported raw lifecycle events have clean events", not missing_clean,
          ", ".join(f"{kind}: {count}" for kind, count in missing_clean))

    correlated_cancels = rows(conn, """
        select r.id
        from raw_app_events r
        join app_events e on e.app_id = r.app_id and e.platform_event_id = r.id
        where r.type = 'SUBSCRIPTION_CHARGE_CANCELED'
          and e.type = 'unsubscribed'
          and (
              exists (
                  select 1 from raw_app_events activation
                  where activation.app_id = r.app_id
                    and activation.shop_gid = r.shop_gid
                    and activation.type in ('SUBSCRIPTION_CHARGE_ACTIVATED',
                                            'SUBSCRIPTION_CHARGE_ACCEPTED')
                    and activation.occurred_at >= r.occurred_at
                    and activation.occurred_at < r.occurred_at + interval '60 seconds'
              )
              or exists (
                  select 1 from raw_app_events activation
                  where activation.app_id = r.app_id
                    and activation.shop_gid = r.shop_gid
                    and activation.charge_gid = r.charge_gid
                    and activation.type in ('SUBSCRIPTION_CHARGE_ACTIVATED',
                                            'SUBSCRIPTION_CHARGE_ACCEPTED')
                    and activation.occurred_at > r.occurred_at - interval '5 seconds'
                    and activation.occurred_at <= r.occurred_at
              )
          )
    """)
    check("Plan-change cancellations are suppressed", not correlated_cancels,
          f"{len(correlated_cancels)} correlated cancellation(s) remain")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} invariant(s) FAILED: {', '.join(FAILURES)}")
        return 1
    print("All invariants hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
