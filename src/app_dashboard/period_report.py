"""Portfolio performance grouped by app over an arbitrary period."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

import psycopg

from app_dashboard.catalog import AppConfig
from app_dashboard.scope import Scope
from app_dashboard.stats import mrr_movements_by_app_between


ZERO = Decimal("0")


@dataclass(frozen=True)
class PeriodRow:
    app: AppConfig
    installs: int = 0
    uninstalls: int = 0
    mrr_gained: Decimal = ZERO
    mrr_lost: Decimal = ZERO
    net_mrr: Decimal = ZERO
    collected: Decimal = ZERO

    @property
    def net_installs(self) -> int:
        return self.installs - self.uninstalls


@dataclass(frozen=True)
class PeriodTotals:
    installs: int
    uninstalls: int
    net_installs: int
    mrr_gained: Decimal
    mrr_lost: Decimal
    net_mrr: Decimal
    collected: Decimal


@dataclass(frozen=True)
class PeriodReport:
    rows: tuple[PeriodRow, ...]
    totals: PeriodTotals
    previous_totals: PeriodTotals

    @property
    def comparison(self) -> dict[str, dict[str, int | Decimal]]:
        return {
            key: {
                "change": getattr(self.totals, key)
                - getattr(self.previous_totals, key)
            }
            for key in PeriodTotals.__dataclass_fields__
        }


def _event_counts(conn, start, end, scope: Scope) -> dict[int, tuple[int, int]]:
    predicate, params = scope.predicate("e")
    rows = conn.execute(
        f"""select e.app_id,
                   count(*) filter (
                       where e.type in ('installed', 'reinstalled')
                   ) as installs,
                   count(*) filter (where e.type = 'uninstalled') as uninstalls
            from app_events e
            where e.occurred_at >= %s and e.occurred_at < %s
              and {predicate}
            group by e.app_id""",
        (start, end, *params),
    ).fetchall()
    return {
        app_id: (installs, uninstalls)
        for app_id, installs, uninstalls in rows
    }


def _cash_by_app(conn, start, end, scope: Scope) -> dict[int, Decimal]:
    predicate, params = scope.predicate("t")
    rows = conn.execute(
        f"""select t.app_id, coalesce(sum(t.net_amount), 0)
            from transactions t
            where t.created_at >= %s and t.created_at < %s
              and {predicate}
            group by t.app_id""",
        (start, end, *params),
    ).fetchall()
    return dict(rows)


def _rows_between(
    conn: psycopg.Connection,
    apps: Sequence[AppConfig],
    start,
    end,
    scope: Scope,
) -> tuple[PeriodRow, ...]:
    events = _event_counts(conn, start, end, scope)
    cash = _cash_by_app(conn, start, end, scope)
    movements = mrr_movements_by_app_between(conn, start, end, scope)
    included = (
        app for app in apps if scope.app_id is None or app.id == scope.app_id
    )
    rows = []
    for app in included:
        installs, uninstalls = events.get(app.id, (0, 0))
        movement = movements.get(app.id, {})
        gained = sum(
            (
                movement.get(kind, ZERO)
                for kind in ("new", "reactivation", "expansion")
            ),
            ZERO,
        )
        lost = -sum(
            (
                movement.get(kind, ZERO)
                for kind in ("contraction", "churned")
            ),
            ZERO,
        )
        rows.append(
            PeriodRow(
                app=app,
                installs=installs,
                uninstalls=uninstalls,
                mrr_gained=gained,
                mrr_lost=lost,
                net_mrr=movement.get("net", ZERO),
                collected=cash.get(app.id, ZERO),
            )
        )
    return tuple(rows)


def _totals(rows: tuple[PeriodRow, ...]) -> PeriodTotals:
    return PeriodTotals(
        installs=sum(row.installs for row in rows),
        uninstalls=sum(row.uninstalls for row in rows),
        net_installs=sum(row.net_installs for row in rows),
        mrr_gained=sum((row.mrr_gained for row in rows), ZERO),
        mrr_lost=sum((row.mrr_lost for row in rows), ZERO),
        net_mrr=sum((row.net_mrr for row in rows), ZERO),
        collected=sum((row.collected for row in rows), ZERO),
    )


def build_period_report(
    conn: psycopg.Connection,
    apps: Sequence[AppConfig],
    start,
    end,
    previous_start,
    previous_end,
    scope: Scope = Scope.all(),
) -> PeriodReport:
    current = _rows_between(conn, apps, start, end, scope)
    previous = _rows_between(
        conn, apps, previous_start, previous_end, scope
    )
    return PeriodReport(current, _totals(current), _totals(previous))


SORT_KEYS: dict[str, Callable[[PeriodRow], object]] = {
    "app": lambda row: row.app.name.casefold(),
    "installs": lambda row: row.installs,
    "uninstalls": lambda row: row.uninstalls,
    "net_installs": lambda row: row.net_installs,
    "mrr_gained": lambda row: row.mrr_gained,
    "mrr_lost": lambda row: row.mrr_lost,
    "net_mrr": lambda row: row.net_mrr,
    "collected": lambda row: row.collected,
}


def normalise_sort(key: str | None, direction: str | None) -> tuple[str, str]:
    safe_key = key if key in SORT_KEYS else "net_mrr"
    safe_direction = direction if direction in {"asc", "desc"} else "desc"
    return safe_key, safe_direction


def sort_rows(
    rows: Sequence[PeriodRow], key: str | None = None, direction: str | None = None
) -> tuple[PeriodRow, ...]:
    key, direction = normalise_sort(key, direction)
    alphabetical = sorted(rows, key=lambda row: row.app.name.casefold())
    return tuple(
        sorted(
            alphabetical,
            key=SORT_KEYS[key],
            reverse=direction == "desc",
        )
    )
