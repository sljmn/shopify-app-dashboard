"""Settlement reporting from Shopify Partner earning events."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg

from app_dashboard.scope import Scope


@dataclass(frozen=True)
class PayoutTotal:
    currency_code: str
    net_amount: Decimal


@dataclass(frozen=True)
class PayoutSettlement:
    settlement_date: date
    currency_code: str
    net_amount: Decimal
    earnings: int


@dataclass(frozen=True)
class PayoutEarning:
    id: str
    app_name: str
    app_slug: str
    event_type: str
    earning_type: str | None
    occurred_at: object
    shop_name: str | None
    shop_domain: str | None
    description: str | None
    gross_amount: Decimal | None
    shopify_fee: Decimal | None
    net_amount: Decimal
    currency_code: str


@dataclass(frozen=True)
class PayoutReport:
    totals: tuple[PayoutTotal, ...]
    settlements: tuple[PayoutSettlement, ...]
    earnings: tuple[PayoutEarning, ...]
    unsettled: int
    selected_date: date | None


def payout_report(
    conn: psycopg.Connection,
    scope: Scope,
    start: date,
    end: date,
    selected_date: date | None = None,
) -> PayoutReport:
    """Return settled earnings for a period and optional daily drill-down."""
    predicate, params = scope.predicate("p")
    period_params = (*params, start, end)

    totals = tuple(
        PayoutTotal(currency_code=row[0], net_amount=row[1])
        for row in conn.execute(
            f"""
            select p.currency_code, sum(p.net_amount)
            from payout_earnings p
            where {predicate}
              and p.settlement_date between %s and %s
            group by p.currency_code
            order by p.currency_code
            """,
            period_params,
        ).fetchall()
    )
    settlements = tuple(
        PayoutSettlement(
            settlement_date=row[0], currency_code=row[1],
            net_amount=row[2], earnings=row[3],
        )
        for row in conn.execute(
            f"""
            select p.settlement_date, p.currency_code,
                   sum(p.net_amount), count(*)
            from payout_earnings p
            where {predicate}
              and p.settlement_date between %s and %s
            group by p.settlement_date, p.currency_code
            order by p.settlement_date desc, p.currency_code
            """,
            period_params,
        ).fetchall()
    )
    unsettled = conn.execute(
        f"""
        select count(*) from payout_earnings p
        where {predicate} and p.settlement_date is null
        """,
        params,
    ).fetchone()[0]

    earnings: tuple[PayoutEarning, ...] = ()
    if selected_date is not None:
        earnings = tuple(
            PayoutEarning(
                id=row[0], app_name=row[1], app_slug=row[2], event_type=row[3],
                earning_type=row[4], occurred_at=row[5], shop_name=row[6],
                shop_domain=row[7], description=row[8], gross_amount=row[9],
                shopify_fee=row[10], net_amount=row[11], currency_code=row[12],
            )
            for row in conn.execute(
                f"""
                select p.id, a.name, a.slug, p.event_type, p.earning_type,
                       p.occurred_at, s.shop_name, s.shop_domain, p.description,
                       p.gross_amount, p.shopify_fee, p.net_amount,
                       p.currency_code
                from payout_earnings p
                join apps a on a.id = p.app_id
                left join shops s
                  on s.app_id = p.app_id and s.shop_gid = p.shop_gid
                where {predicate} and p.settlement_date = %s
                order by p.occurred_at desc, p.id
                """,
                (*params, selected_date),
            ).fetchall()
        )

    return PayoutReport(
        totals=totals,
        settlements=settlements,
        earnings=earnings,
        unsettled=unsettled,
        selected_date=selected_date,
    )
