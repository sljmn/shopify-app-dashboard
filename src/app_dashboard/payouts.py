"""Settlement reporting from Shopify Partner earning events."""

from dataclasses import dataclass
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

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
class PayoutWindow:
    start: date
    end: date
    payment_date: date
    paid: Decimal
    due: Decimal
    billed: Decimal
    upcoming: Decimal
    estimated: bool

    @property
    def total(self) -> Decimal:
        return self.paid + self.due + self.billed + self.upcoming


@dataclass(frozen=True)
class PayoutReport:
    totals: tuple[PayoutTotal, ...]
    settlements: tuple[PayoutSettlement, ...]
    earnings: tuple[PayoutEarning, ...]
    unsettled: int
    selected_date: date | None
    cashflow: tuple[PayoutWindow, ...]
    cashflow_currency: str | None
    cashflow_max: Decimal


def _month_end(value: date) -> date:
    return value.replace(day=monthrange(value.year, value.month)[1])


def _shift_month(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    year, zero_month = divmod(index, 12)
    return date(year, zero_month + 1, 1)


def _window_containing(value: date) -> tuple[date, date]:
    if value.day <= 15:
        return value.replace(day=1), value.replace(day=15)
    return value.replace(day=16), _month_end(value)


def _previous_window(start: date) -> tuple[date, date]:
    if start.day == 16:
        return start.replace(day=1), start.replace(day=15)
    previous_month = _shift_month(start, -1)
    return previous_month.replace(day=16), _month_end(previous_month)


def _next_window(end: date) -> tuple[date, date]:
    if end.day == 15:
        return end.replace(day=16), _month_end(end)
    next_month = _shift_month(end, 1)
    return next_month, next_month.replace(day=15)


def _payment_date(end: date) -> date:
    """Match the twice-monthly dates shown in Shopify's Mantle payout card."""
    if end.day == 15:
        return end.replace(day=20)
    return _shift_month(end, 1).replace(day=6)


def _projected_net(
    conn: psycopg.Connection, scope: Scope, start: date, end: date,
) -> Decimal:
    """Project charges actually due in a window, respecting annual cadence."""
    predicate, params = scope.predicate("sub")
    rows = conn.execute(
        f"""
        select sub.monthly_amount,
               coalesce(current_sub.billing_period, sub.billing_type,
                        latest.billing_interval, 'EVERY_30_DAYS'),
               coalesce(latest.created_at, sub.converted_at),
               latest.gross_amount, latest.net_amount
        from subscriptions sub
        join shops s on s.app_id = sub.app_id and s.shop_gid = sub.shop_gid
        left join active_subscriptions current_sub
          on current_sub.app_id = sub.app_id
         and current_sub.shop_gid = sub.shop_gid
        left join lateral (
            select t.created_at, t.billing_interval, t.gross_amount, t.net_amount
            from transactions t
            where t.app_id = sub.app_id and t.shop_gid = sub.shop_gid
              and t.type = 'AppSubscriptionSale' and t.gross_amount > 0
            order by t.created_at desc, t.id desc limit 1
        ) latest on true
        where {predicate}
          and sub.churned_at is null
          and s.install_state = 'installed'
          and sub.monthly_amount > 0.01
          and (current_sub.trial_ends_at is null
               or current_sub.trial_ends_at <= now())
        """,
        params,
    ).fetchall()
    projected = Decimal("0")
    for monthly, interval, anchor, gross, net in rows:
        if anchor is None:
            continue
        cadence = timedelta(days=365 if interval == "ANNUAL" else 30)
        charge_date = anchor.date()
        while charge_date < start:
            charge_date += cadence
        if charge_date > end:
            continue
        charge = monthly * (12 if interval == "ANNUAL" else 1)
        ratio = (net / gross) if gross and net is not None else Decimal("0.971")
        projected += charge * ratio
    return projected.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _cashflow(
    conn: psycopg.Connection, scope: Scope, today: date,
) -> tuple[tuple[PayoutWindow, ...], str | None, Decimal]:
    current = _window_containing(today)
    windows = (_previous_window(current[0]), current, _next_window(current[1]))
    predicate, params = scope.predicate("p")
    rows = conn.execute(
        f"""
        select (p.occurred_at at time zone 'Europe/Amsterdam')::date,
               p.settlement_date, p.net_amount, p.currency_code
        from payout_earnings p
        where {predicate}
          and p.occurred_at >= %s::date
          and p.occurred_at < (%s::date + interval '1 day')
        order by p.occurred_at, p.id
        """,
        (*params, windows[0][0], windows[-1][1]),
    ).fetchall()
    currencies = {row[3] for row in rows}
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    projection = _projected_net(conn, scope, *windows[-1])

    result = []
    for index, (start, end) in enumerate(windows):
        paid = due = billed = Decimal("0")
        for occurred_on, settlement_date, amount, row_currency in rows:
            if not (start <= occurred_on <= end):
                continue
            # A mixed-currency total would be false precision. The overview is
            # withheld in that rare case; the exact ledger remains available.
            if currency is None or row_currency != currency:
                continue
            if settlement_date is None:
                billed += amount
            elif _payment_date(end) < today:
                paid += amount
            else:
                due += amount
        upcoming = Decimal("0")
        estimated = index == len(windows) - 1
        if estimated and currency in (None, "USD"):
            upcoming = max(projection - paid - due - billed, Decimal("0"))
            currency = currency or "USD"
        result.append(PayoutWindow(
            start=start, end=end, payment_date=_payment_date(end), paid=paid,
            due=due, billed=billed, upcoming=upcoming, estimated=estimated,
        ))
    maximum = max((row.total for row in result), default=Decimal("0"))
    return tuple(result), currency, maximum


def payout_report(
    conn: psycopg.Connection,
    scope: Scope,
    start: date,
    end: date,
    selected_date: date | None = None,
    *,
    today: date | None = None,
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

    cashflow, cashflow_currency, cashflow_max = _cashflow(
        conn, scope, today or datetime.now(timezone.utc).date()
    )
    return PayoutReport(
        totals=totals,
        settlements=settlements,
        earnings=earnings,
        unsettled=unsettled,
        selected_date=selected_date,
        cashflow=cashflow,
        cashflow_currency=cashflow_currency,
        cashflow_max=cashflow_max,
    )
