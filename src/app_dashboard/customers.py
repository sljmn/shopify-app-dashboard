from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from app_dashboard.scope import Scope

SORTS = {
    "installed_at": "s.installed_at desc nulls last",
    "shop_name": "s.shop_name asc",
    "country": "s.country asc",
    "industry": "s.industry asc",
    "app_name": "a.name asc, s.shop_name asc",
}


INSTALL_STATES = ("installed", "uninstalled")
PAYING_THRESHOLD = Decimal("0.01")

# Billing intervals a shop can be filtered by. A whitelist rather than a facet
# query: these two are the whole plan catalogue, and an unrecognised value has
# to fall through to "no filter" rather than to an empty page.
PLAN_INTERVALS = ("EVERY_30_DAYS", "ANNUAL")


def _filters(
    industry, country, search, install_state, plan, source, keyword, scope: Scope
) -> tuple[str, list]:
    """Build the additive WHERE shared by the page query and its count.

    Every value is a bound parameter; nothing here is interpolated into SQL.
    """
    predicate, scope_params = scope.predicate("s")
    where = [predicate]
    params = list(scope_params)
    if install_state in INSTALL_STATES:
        where.append("s.install_state = %s")
        params.append(install_state)
    if plan in PLAN_INTERVALS:
        # Live subscriptions only: this is "who is on annual right now", which
        # is what the plan-mix bars on Overview count and therefore what they
        # have to link to. A shop that churned off annual is not on annual.
        where.append(
            """exists (
                   select 1 from subscriptions sub
                   join charges c on c.app_id = sub.app_id and c.gid = sub.id
                   where sub.app_id = s.app_id and sub.shop_gid = s.shop_gid
                     and sub.churned_at is null
                     and coalesce(sub.billing_type, c.plan_interval) = %s
               )"""
        )
        params.append(plan)
    if industry is not None:
        where.append("s.industry = %s")
        params.append(industry)
    if country is not None:
        where.append("s.country = %s")
        params.append(country)
    if search is not None:
        where.append("(s.shop_name ilike %s or s.shop_domain ilike %s or a.name ilike %s)")
        needle = f"%{search}%"
        params.extend([needle, needle, needle])
    if source is not None:
        where.append(
            """exists (select 1 from aso_install_sources attr
                       where attr.app_id=s.app_id
                         and lower(attr.shop_domain)=lower(s.shop_domain)
                         and attr.source=%s)"""
        )
        params.append(source)
    if keyword is not None:
        where.append(
            """exists (select 1 from aso_install_sources attr
                       where attr.app_id=s.app_id
                         and lower(attr.shop_domain)=lower(s.shop_domain)
                         and attr.source_value ilike %s)"""
        )
        params.append(f"%{keyword}%")
    return f"where {' and '.join(where)}", params


def list_customers(conn: psycopg.Connection, *, industry=None, country=None,
                    search=None, install_state=None, plan=None,
                    source=None, keyword=None,
                    sort="installed_at", limit=100, offset=0,
                    scope: Scope = Scope.all()) -> list[dict]:
    """Filter shops by industry/country/state/plan/search (additive), sorted via whitelist."""
    where_sql, params = _filters(
        industry, country, search, install_state, plan, source, keyword, scope
    )
    order_sql = SORTS.get(sort, SORTS["installed_at"])
    params.extend([limit, offset])

    cur = conn.cursor(row_factory=dict_row)
    # Columns named, not `select *`. shops still HAS owner_name and email; they
    # are emptied by migration 008 and the template does not render them, so
    # nothing leaks today. But `select *` means the no-PII rule is enforced by
    # two downstream strippers rather than by the query, and this dict is
    # rendered into a copyable .md document. Enforce it where the data is read.
    cur.execute(
        f"""select s.app_id, a.slug as app_slug, a.name as app_name,
                   s.shop_gid, s.shop_domain, s.shop_name, s.country, s.industry,
                   s.install_state, s.installed_at, s.uninstalled_at,
                   s.uninstall_reason, s.uninstall_description, s.reviewed_at,
                   current_sub.monthly_amount,
                   coalesce(current_sub.plan_interval, trial.billing_period)
                       as plan_interval,
                   trial.trial_ends_at,
                   latest_event.type as latest_event_type,
                   latest_event.occurred_at as latest_event_at,
                   attribution.source as attribution_source,
                   attribution.source_type as attribution_source_type,
                   attribution.source_value as attribution_keyword
            from shops s
            join apps a on a.id = s.app_id
            left join lateral (
                select sub.monthly_amount,
                       coalesce(sub.billing_type, c.plan_interval,
                                latest_payment.billing_interval)
                           as plan_interval
                from subscriptions sub
                left join charges c
                  on c.app_id = sub.app_id and c.gid = sub.id
                left join lateral (
                    select t.billing_interval
                    from transactions t
                    where t.app_id = sub.app_id
                      and t.shop_gid = sub.shop_gid
                      and t.billing_interval is not null
                    order by t.created_at desc limit 1
                ) latest_payment on true
                where sub.app_id = s.app_id
                  and sub.shop_gid = s.shop_gid
                  and sub.churned_at is null
                order by sub.converted_at desc nulls last, sub.id
                limit 1
            ) current_sub on true
            left join active_subscriptions trial
              on trial.app_id = s.app_id
             and trial.shop_gid = s.shop_gid
             and trial.trial_ends_at > now()
            left join lateral (
                select e.type, e.occurred_at
                from app_events e
                where e.app_id = s.app_id and e.shop_gid = s.shop_gid
                order by e.occurred_at desc, e.id desc limit 1
            ) latest_event on true
            left join lateral (
                select attr.source, attr.source_type, attr.source_value
                from aso_install_sources attr
                where attr.app_id=s.app_id
                  and lower(attr.shop_domain)=lower(s.shop_domain)
                order by attr.installed_on desc, attr.observed_at desc
                limit 1
            ) attribution on true
            {where_sql} order by {order_sql} limit %s offset %s""",
        params,
    )
    rows = cur.fetchall()
    for row in rows:
        amount = row["monthly_amount"]
        in_trial = row["trial_ends_at"] is not None
        installed = row["install_state"] == "installed"
        paying = amount is not None and amount > PAYING_THRESHOLD

        if in_trial:
            row["plan_label"] = "Trial"
        elif amount is None:
            row["plan_label"] = None
        elif paying:
            row["plan_label"] = (
                "Annual" if row["plan_interval"] == "ANNUAL" else "Monthly"
            )
        else:
            row["plan_label"] = "Free"

        row["mrr"] = amount if installed and not in_trial and paying else Decimal("0")
        if not installed:
            row["customer_status"] = "Uninstalled"
        elif in_trial:
            row["customer_status"] = "Trial"
        elif paying:
            row["customer_status"] = "Paying"
        elif amount is not None:
            row["customer_status"] = "Free"
        elif row["latest_event_type"] == "unsubscribed":
            row["customer_status"] = "Cancelled"
        else:
            row["customer_status"] = "Installed"
    return rows


def count_customers(conn: psycopg.Connection, *, industry=None, country=None,
                    search=None, install_state=None, plan=None,
                    source=None, keyword=None,
                    scope: Scope = Scope.all()) -> int:
    """How many rows the same filters match, ignoring limit/offset. Without this
    the page can only say "here are 50 rows", not "50 of 119"."""
    where_sql, params = _filters(
        industry, country, search, install_state, plan, source, keyword, scope
    )
    return conn.execute(
        f"select count(*) from shops s join apps a on a.id=s.app_id {where_sql}",
        params,
    ).fetchone()[0]


# Lifecycle rows the timeline renders, and the label each gets. usage_events are
# folded in separately (they have their own table and their own vocabulary).
EVENT_LABELS = {
    "installed": "Installed",
    "reinstalled": "Reinstalled",
    "uninstalled": "Uninstalled",
    "subscribed": "Subscribed",
    "resubscribed": "Resubscribed",
    "upgraded": "Upgraded",
    "downgraded": "Downgraded",
    "unsubscribed": "Subscription ended",
    "subscription_frozen": "Subscription frozen",
    "subscription_unfrozen": "Subscription unfrozen",
    "subscription_reconciled": "Subscription reconciled with Shopify",
    "charge_abandoned": "Charge abandoned",
}


def customer_detail(
    conn: psycopg.Connection,
    shop_gid: str,
    scope: Scope = Scope.all(),
) -> dict | None:
    """Return every matching app installation for one stable Shopify shop GID."""
    predicate, params = scope.predicate("s")
    cur = conn.cursor(row_factory=dict_row)
    cur.execute(
        f"""
        select s.shop_gid, s.shop_name, s.shop_domain, s.country, s.industry,
               s.install_state, s.installed_at, s.uninstalled_at,
               s.uninstall_reason, s.uninstall_description, s.reviewed_at,
               a.id as app_id, a.slug as app_slug, a.name as app_name
        from shops s
        join apps a on a.id = s.app_id
        where s.shop_gid = %s and {predicate}
        order by a.name, a.id
        """,
        (shop_gid, *params),
    )
    installations = cur.fetchall()
    if not installations:
        return None

    app_sections = []
    for installation in installations:
        app_id = installation["app_id"]
        lifecycle = conn.execute(
            """
            select e.type, e.occurred_at, e.plan_amount, e.plan_interval,
                   e.net_change, e.uninstall_reason, e.uninstall_description,
                   r.type as raw_type
            from app_events e
            left join raw_app_events r
              on r.app_id = e.app_id and r.id = e.platform_event_id
            where e.app_id = %s and e.shop_gid = %s
            order by e.occurred_at, e.id
            """,
            (app_id, shop_gid),
        ).fetchall()

        timeline = []
        for (kind, at, amount, interval, net_change, reason, note,
             raw_type) in lifecycle:
            label = EVENT_LABELS.get(kind, kind)
            if kind == "uninstalled" and raw_type == "RELATIONSHIP_DEACTIVATED":
                label = "Store closed or frozen by Shopify"
            timeline.append({
                "at": at,
                "kind": kind,
                "label": label,
                "amount": amount,
                "interval": interval,
                "net_change": net_change,
                "reason": reason,
                "note": note,
                "chose_to_leave": raw_type == "RELATIONSHIP_UNINSTALLED",
            })

        payments = conn.execute(
            """
            select id, type, created_at, gross_amount, shopify_fee, net_amount,
                   billing_interval
            from transactions
            where app_id = %s and shop_gid = %s
            order by created_at desc
            """,
            (app_id, shop_gid),
        ).fetchall()
        money = {
            "gross": sum((payment[3] or 0) for payment in payments),
            "net": sum((payment[5] or 0) for payment in payments),
            "count": len(payments),
            "first_at": min((payment[2] for payment in payments), default=None),
            "last_at": max((payment[2] for payment in payments), default=None),
        }
        money["taken"] = money["gross"] - money["net"]

        subscription = conn.execute(
            """
            select sub.monthly_amount, sub.converted_at, sub.churned_at,
                   coalesce(sub.billing_type, c.plan_interval, (
                       select t.billing_interval from transactions t
                       where t.app_id = sub.app_id
                         and t.shop_gid = sub.shop_gid
                         and t.billing_interval is not null
                       order by t.created_at desc limit 1
                   ))
            from subscriptions sub
            left join charges c on c.app_id = sub.app_id and c.gid = sub.id
            where sub.app_id = %s and sub.shop_gid = %s
            order by sub.converted_at desc nulls last limit 1
            """,
            (app_id, shop_gid),
        ).fetchone()

        trial = conn.execute(
            """
            select current_sub.trial_ends_at,
                   current_sub.cancel_at_end_of_cycle,
                   current_sub.item_handle,
                   current_sub.item_description,
                   current_sub.currency_code,
                   sub.monthly_amount
            from active_subscriptions current_sub
            left join subscriptions sub
              on sub.app_id = current_sub.app_id
             and sub.id = current_sub.legacy_subscription_id
             and sub.shop_gid = current_sub.shop_gid
             and sub.churned_at is null
            where current_sub.app_id = %s
              and current_sub.shop_gid = %s
              and current_sub.trial_ends_at > now()
            """,
            (app_id, shop_gid),
        ).fetchone()

        usage = conn.execute(
            """
            select event_type, count(*), min(occurred_at), max(occurred_at)
            from usage_events
            where app_id = %s and shop_gid = %s
            group by event_type order by 2 desc
            """,
            (app_id, shop_gid),
        ).fetchall()
        attribution_row = conn.execute(
            """
            select installed_on, source, source_type, source_value,
                   locale, country, device
            from aso_install_sources
            where app_id=%s and lower(shop_domain)=lower(%s)
            order by installed_on desc, observed_at desc limit 1
            """,
            (app_id, installation["shop_domain"]),
        ).fetchone() if installation["shop_domain"] else None
        installs = [
            event for event in timeline
            if event["kind"] in ("installed", "reinstalled")
        ]
        shop = {
            key: installation[key]
            for key in (
                "shop_gid", "shop_name", "shop_domain", "country", "industry",
                "install_state", "installed_at", "uninstalled_at",
                "uninstall_reason", "uninstall_description", "reviewed_at",
            )
        }
        app_sections.append({
            "app": {
                "id": app_id,
                "slug": installation["app_slug"],
                "name": installation["app_name"],
            },
            "shop": shop,
            "timeline": timeline,
            "payments": [
                {"id": identifier, "type": kind, "at": at, "gross": gross,
                 "fee": fee, "net": net, "interval": interval}
                for identifier, kind, at, gross, fee, net, interval in payments
            ],
            "money": money,
            "subscription": (
                {"monthly_amount": subscription[0], "converted_at": subscription[1],
                 "churned_at": subscription[2], "plan_interval": subscription[3]}
                if subscription else None
            ),
            "trial": (
                {"trial_ends_at": trial[0], "cancel_at_end_of_cycle": trial[1],
                 "item_handle": trial[2], "item_description": trial[3],
                 "currency_code": trial[4], "monthly_amount": trial[5]}
                if trial else None
            ),
            "usage": [
                {"event_type": kind, "count": count, "first_at": first,
                 "last_at": last}
                for kind, count, first, last in usage
            ],
            "attribution": (
                {"installed_on": attribution_row[0], "source": attribution_row[1],
                 "source_type": attribution_row[2], "source_value": attribution_row[3],
                 "locale": attribution_row[4], "country": attribution_row[5],
                 "device": attribution_row[6]}
                if attribution_row else None
            ),
            "first_install_at": installs[0]["at"] if installs else None,
            "install_count": len(installs),
            "current_state": (
                "uninstalled" if next(
                    (event["kind"] for event in reversed(timeline)
                     if event["kind"] in ("installed", "reinstalled", "uninstalled")),
                    shop["install_state"],
                ) == "uninstalled" else "installed"
            ),
        })

    display_name = next(
        (
            section["shop"]["shop_name"] or section["shop"]["shop_domain"]
            for section in app_sections
            if section["shop"]["shop_name"] or section["shop"]["shop_domain"]
        ),
        shop_gid,
    )
    return {
        "shop_gid": shop_gid,
        "display_name": display_name,
        "domains": sorted({
            section["shop"]["shop_domain"]
            for section in app_sections
            if section["shop"]["shop_domain"]
        }),
        "apps": app_sections,
    }


def distinct_facets(
    conn: psycopg.Connection, scope: Scope = Scope.all()
) -> dict:
    """Distinct non-null industries/countries for filter dropdowns."""
    predicate, params = scope.predicate("shops")
    industries = [
        row[0] for row in conn.execute(
            f"""select distinct industry from shops
                where industry is not null and {predicate} order by industry""",
            params,
        ).fetchall()
    ]
    countries = [
        row[0] for row in conn.execute(
            f"""select distinct country from shops
                where country is not null and {predicate} order by country""",
            params,
        ).fetchall()
    ]
    states = [
        row[0] for row in conn.execute(
            f"""select distinct install_state from shops
                where install_state is not null and {predicate}
                order by install_state""",
            params,
        ).fetchall()
    ]
    source_predicate, source_params = scope.predicate("shops")
    sources = [
        row[0] for row in conn.execute(
            f"""select distinct attr.source
                from aso_install_sources attr
                join shops on shops.app_id=attr.app_id
                          and lower(shops.shop_domain)=lower(attr.shop_domain)
                where attr.source <> '' and {source_predicate}
                order by attr.source""",
            source_params,
        ).fetchall()
    ]
    return {
        "industries": industries, "countries": countries, "states": states,
        "sources": sources,
    }
