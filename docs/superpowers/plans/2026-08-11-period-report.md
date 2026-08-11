# Period Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dedicated, app-aware period report that compares lifecycle activity, MRR movement, and collected revenue over a selected window and its immediately preceding equivalent.

**Architecture:** Parse all period inputs in a pure `periods.py` module that returns validated UTC boundaries based on Amsterdam local time. Extend the existing corrected-subscription movement logic to return grouped per-app buckets, then combine it with grouped lifecycle and transaction queries in a focused `period_report.py` service. A thin FastAPI route renders a new template with server-side preset and sorting links, preserving the global app scope.

**Tech Stack:** Python 3.13, FastAPI, psycopg/PostgreSQL, Jinja2, existing dashboard CSS and Flatpickr progressive enhancement, pytest.

---

## File map

- Create `src/app_dashboard/periods.py`: pure preset/custom period parsing, Amsterdam-to-UTC boundaries, validation, and previous-window calculation.
- Create `src/app_dashboard/period_report.py`: grouped lifecycle/cash aggregation, row/totals models, sorting, and current-versus-previous report composition.
- Modify `src/app_dashboard/stats.py`: expose corrected subscription MRR movement grouped by app and keep the existing aggregate function backed by it.
- Modify `src/app_dashboard/metrics.py`: register definitions and directionality for the seven period summary metrics.
- Modify `src/app_dashboard/web.py`: add the authenticated `/period` route and prepare retained preset/sort links.
- Create `src/app_dashboard/templates/period.html`: preset controls, custom date form, summary cards, sortable per-app table, and clear empty/future states.
- Modify `src/app_dashboard/templates/_macros.html`: allow a caller-supplied comparison label while retaining the current Overview default.
- Modify `src/app_dashboard/templates/base.html`: add Period to navigation and narrowly scoped responsive styles.
- Create `tests/test_periods.py`: deterministic unit tests for presets, custom bounds, DST, future periods, and invalid inputs.
- Create `tests/test_period_report.py`: database tests for grouped app results, MRR rules, cash, comparisons, sorting, and total invariants.
- Modify `tests/test_stats.py`: regression tests for grouped MRR buckets and the existing aggregate API.
- Modify `tests/test_web.py`: route, navigation, retained query parameter, empty-state, and HTML behavior tests.

### Task 1: Resolve safe period boundaries

**Files:**
- Create: `src/app_dashboard/periods.py`
- Create: `tests/test_periods.py`

- [ ] **Step 1: Write failing tests for the default and rolling presets**

```python
from datetime import datetime, timezone

from app_dashboard.periods import resolve_period


NOW = datetime(2026, 8, 11, 14, 30, tzinfo=timezone.utc)


def test_default_is_the_last_30_days_ending_now():
    selected = resolve_period(None, None, None, now=NOW)
    assert selected.preset == "30d"
    assert selected.start == datetime(2026, 7, 12, 14, 30, tzinfo=timezone.utc)
    assert selected.end == NOW
    assert selected.previous_end == selected.start
    assert selected.previous_start == datetime(2026, 6, 12, 14, 30,
                                               tzinfo=timezone.utc)
    assert selected.error is None


def test_rolling_presets_have_the_requested_duration():
    assert resolve_period("7d", None, None, now=NOW).duration.days == 7
    assert resolve_period("90d", None, None, now=NOW).duration.days == 90
```

- [ ] **Step 2: Run the rolling tests and verify the module is missing**

Run: `uv run --frozen pytest tests/test_periods.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: app_dashboard.periods`.

- [ ] **Step 3: Add custom, calendar-month, DST, and invalid-input tests**

```python
def test_custom_dates_are_inclusive_amsterdam_calendar_days():
    selected = resolve_period("custom", "2026-03-29", "2026-03-29", now=NOW)
    assert selected.start == datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc)
    assert selected.end == datetime(2026, 3, 29, 22, 0, tzinfo=timezone.utc)
    assert selected.duration.total_seconds() == 23 * 60 * 60


def test_last_month_uses_local_calendar_boundaries():
    selected = resolve_period("last_month", None, None, now=NOW)
    assert selected.start == datetime(2026, 6, 30, 22, 0, tzinfo=timezone.utc)
    assert selected.end == datetime(2026, 7, 31, 22, 0, tzinfo=timezone.utc)


def test_invalid_custom_dates_fall_back_without_raising():
    selected = resolve_period("custom", "bad", "2026-08-01", now=NOW)
    assert selected.preset == "30d"
    assert selected.error == "Choose a valid start and end date."


def test_reversed_and_overlong_custom_ranges_are_rejected():
    reversed_range = resolve_period("custom", "2026-08-10", "2026-08-01", now=NOW)
    assert reversed_range.error == "The end date must be on or after the start date."
    overlong = resolve_period("custom", "2024-01-01", "2026-08-01", now=NOW)
    assert overlong.error == "A custom period may span at most two years."


def test_a_wholly_future_custom_period_is_marked():
    selected = resolve_period("custom", "2026-08-14", "2026-08-15", now=NOW)
    assert selected.is_future is True
```

- [ ] **Step 4: Implement the immutable selection and resolver**

```python
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone

from app_dashboard.display_time import DISPLAY_TIMEZONE, local_day_bounds


PRESETS = ("7d", "30d", "90d", "this_month", "last_month", "custom")
PRESET_LABELS = {
    "7d": "7 days",
    "30d": "30 days",
    "90d": "90 days",
    "this_month": "This month",
    "last_month": "Last month",
    "custom": "Custom",
}
MAX_CUSTOM_DAYS = 731


@dataclass(frozen=True)
class PeriodSelection:
    preset: str
    start: datetime
    end: datetime
    previous_start: datetime
    previous_end: datetime
    display_start: date
    display_end: date
    input_start: str = ""
    input_end: str = ""
    error: str | None = None
    now: datetime | None = None

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def is_future(self) -> bool:
        return bool(self.now and self.start >= self.now)

    def query_items(self) -> tuple[tuple[str, str], ...]:
        if self.preset != "custom":
            return (("period", self.preset),)
        return (("period", "custom"), ("start", self.input_start),
                ("end", self.input_end))


def _selection(preset: str, start: datetime, end: datetime,
               now: datetime, input_start: str = "",
               input_end: str = "") -> PeriodSelection:
    duration = end - start
    return PeriodSelection(
        preset=preset, start=start, end=end,
        previous_start=start - duration, previous_end=start,
        display_start=start.astimezone(DISPLAY_TIMEZONE).date(),
        display_end=(end - timedelta(microseconds=1)).astimezone(
            DISPLAY_TIMEZONE).date(),
        input_start=input_start, input_end=input_end, now=now,
    )


def resolve_period(preset: str | None, start_text: str | None,
                   end_text: str | None, *,
                   now: datetime | None = None) -> PeriodSelection:
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(DISPLAY_TIMEZONE)
    key = preset if preset in PRESETS else "30d"
    if key in {"7d", "30d", "90d"}:
        days = int(key[:-1])
        return _selection(key, now - timedelta(days=days), now, now)
    if key == "this_month":
        local_start = datetime.combine(local_now.date().replace(day=1), time.min,
                                       tzinfo=DISPLAY_TIMEZONE)
        return _selection(key, local_start.astimezone(timezone.utc), now, now)
    if key == "last_month":
        this_start = local_now.date().replace(day=1)
        last_end = this_start - timedelta(days=1)
        start_utc, _ = local_day_bounds(last_end.replace(day=1))
        end_utc, _ = local_day_bounds(this_start)
        return _selection(key, start_utc, end_utc, now)

    raw_start, raw_end = start_text or "", end_text or ""
    try:
        start_date, end_date = date.fromisoformat(raw_start), date.fromisoformat(raw_end)
    except ValueError:
        return replace(resolve_period("30d", None, None, now=now),
                       input_start=raw_start, input_end=raw_end,
                       error="Choose a valid start and end date.")
    if end_date < start_date:
        return replace(resolve_period("30d", None, None, now=now),
                       input_start=raw_start, input_end=raw_end,
                       error="The end date must be on or after the start date.")
    if (end_date - start_date).days + 1 > MAX_CUSTOM_DAYS:
        return replace(resolve_period("30d", None, None, now=now),
                       input_start=raw_start, input_end=raw_end,
                       error="A custom period may span at most two years.")
    start_utc, _ = local_day_bounds(start_date)
    _, end_utc = local_day_bounds(end_date)
    return _selection("custom", start_utc, end_utc, now, raw_start, raw_end)
```

- [ ] **Step 5: Run period parsing tests**

Run: `uv run --frozen pytest tests/test_periods.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit the period resolver**

```bash
git add src/app_dashboard/periods.py tests/test_periods.py
git commit -m "Define trustworthy Amsterdam period boundaries"
```

### Task 2: Group corrected MRR movement by app

**Files:**
- Modify: `src/app_dashboard/stats.py`
- Modify: `tests/test_stats.py`

- [ ] **Step 1: Write a failing grouped-MRR regression test**

```python
def test_mrr_movements_between_are_grouped_by_app_and_sum_to_aggregate(
    db, app_factory, test_app
):
    beta = app_factory(slug="beta", name="Beta")
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 11, tzinfo=timezone.utc)
    _shop(db, "alpha-new", app_id=test_app.id)
    _shop(db, "alpha-transient", app_id=test_app.id)
    _shop(db, "beta-gone", app_id=beta.id)
    db.execute(
        """insert into subscriptions
               (app_id, id, shop_gid, monthly_amount, converted_at, churned_at)
           values (%s, 'alpha-sub', 'alpha-new', 24, %s, null),
                  (%s, 'alpha-temp', 'alpha-transient', 5, %s, %s),
                  (%s, 'beta-sub', 'beta-gone', 12, %s, %s)""",
        (test_app.id, start + timedelta(days=1), test_app.id,
         start + timedelta(days=3), start + timedelta(days=4), beta.id,
         start - timedelta(days=20), start + timedelta(days=2)),
    )
    db.commit()

    grouped = mrr_movements_by_app_between(db, start, end)
    # The temporary $5 subscription is visible in both gross sides even though
    # it has no effect on the end-to-end net change.
    assert grouped[test_app.id]["new"] == Decimal("29.00")
    assert grouped[test_app.id]["churned"] == Decimal("-5.00")
    assert grouped[test_app.id]["net"] == Decimal("24.00")
    assert grouped[beta.id]["churned"] == Decimal("-12.00")
    aggregate = mrr_movement_between(db, start, end)
    for key in (*MOVEMENT_KINDS, "net"):
        assert aggregate[key] == sum(row[key] for row in grouped.values())
```

- [ ] **Step 2: Run the grouped test and verify it fails**

Run: `uv run --frozen pytest tests/test_stats.py::test_mrr_movements_between_are_grouped_by_app_and_sum_to_aggregate -q`

Expected: FAIL because `mrr_movements_by_app_between` does not exist.

- [ ] **Step 3: Attribute every corrected MRR transition inside the window**

Add `mrr_movements_by_app_between(conn, start, end, scope)` next to `mrr_movement_between`. Reuse the existing `_billable` query and `_attribute` classifier. Group subscription starts and ends by timestamp per shop so a subscription gained and lost inside one period appears in both gross sides while its net remains zero:

```python
def mrr_movements_by_app_between(
    conn: psycopg.Connection, start, end, scope: Scope = Scope.all()
) -> dict[int, dict[str, Decimal]]:
    predicate, params = scope.predicate("subscriptions")
    billable, billable_params = _billable("subscriptions")
    rows = conn.execute(
        f"""select app_id, shop_gid, coalesce(monthly_amount, 0),
                   converted_at, churned_at
            from subscriptions
            where converted_at is not null and {predicate} and {billable}""",
        (*params, *billable_params),
    ).fetchall()

    by_shop = {}
    for app_id, shop_gid, amount, converted_at, churned_at in rows:
        by_shop.setdefault((app_id, shop_gid), []).append(
            (amount, converted_at, churned_at))

    grouped: dict[int, dict[str, Decimal]] = {}
    for key, subscriptions in by_shop.items():
        app_id, _ = key
        bucket = grouped.setdefault(
            app_id, {kind: Decimal("0") for kind in MOVEMENT_KINDS})
        current = sum(
            (amount for amount, converted_at, churned_at in subscriptions
             if converted_at < start and (churned_at is None or churned_at >= start)),
            Decimal("0"),
        )
        ever_paid = any(converted_at < start
                        for _, converted_at, _ in subscriptions)
        changes = {}
        for amount, converted_at, churned_at in subscriptions:
            if start <= converted_at < end:
                changes.setdefault(converted_at, [Decimal("0"), Decimal("0")])[0] += amount
            if churned_at is not None and start <= churned_at < end:
                changes.setdefault(churned_at, [Decimal("0"), Decimal("0")])[1] += amount
        for instant in sorted(changes):
            gained, lost = changes[instant]
            previous = current
            current += gained - lost
            _attribute(bucket, previous, current, returning=ever_paid)
            if current > 0:
                ever_paid = True
    for bucket in grouped.values():
        bucket["net"] = sum(bucket.values())
    return grouped
```

Replace the body after the docstring of `mrr_movement_between` with an aggregation over this function, keeping its return shape unchanged:

```python
    grouped = mrr_movements_by_app_between(conn, start, end, scope)
    total = {kind: Decimal("0") for kind in MOVEMENT_KINDS}
    for bucket in grouped.values():
        for kind in MOVEMENT_KINDS:
            total[kind] += bucket[kind]
    total["net"] = sum(total.values())
    return total
```

- [ ] **Step 4: Run movement tests**

Run: `uv run --frozen pytest tests/test_stats.py -q`

Expected: all stats tests PASS, including the grouped-to-aggregate invariant.

- [ ] **Step 5: Commit grouped MRR movement**

```bash
git add src/app_dashboard/stats.py tests/test_stats.py
git commit -m "Expose corrected MRR movement by app"
```

### Task 3: Compose the period report without per-app queries

**Files:**
- Create: `src/app_dashboard/period_report.py`
- Create: `tests/test_period_report.py`

- [ ] **Step 1: Write a failing report aggregation test**

```python
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app_dashboard.period_report import build_period_report
from app_dashboard.scope import Scope


def insert_event(db, app_id, event_id, kind, occurred_at, shop_gid="shop"):
    db.execute(
        """insert into app_events
               (app_id, platform_event_id, type, occurred_at, shop_gid)
           values (%s, %s, %s, %s, %s)""",
        (app_id, event_id, kind, occurred_at, shop_gid),
    )


def insert_subscription(db, app_id, sub_id, shop_gid, amount, converted_at,
                        churned_at=None):
    db.execute(
        """insert into subscriptions
               (app_id, id, shop_gid, monthly_amount, converted_at, churned_at)
           values (%s, %s, %s, %s, %s, %s)""",
        (app_id, sub_id, shop_gid, amount, converted_at, churned_at),
    )


def insert_transaction(db, app_id, txn_id, net, created_at,
                       kind="AppSubscriptionSale"):
    db.execute(
        """insert into transactions
               (app_id, id, type, created_at, shop_gid, gross_amount,
                shopify_fee, net_amount, currency_code)
           values (%s, %s, %s, %s, 'shop', %s, 0, %s, 'USD')""",
        (app_id, txn_id, kind, created_at, net, net),
    )


def test_period_report_combines_events_mrr_and_cash_by_app(
    db, app_factory, test_app
):
    beta = app_factory(slug="beta", name="Beta")
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 11, tzinfo=timezone.utc)
    insert_event(db, test_app.id, "a-install", "installed", start + timedelta(days=1))
    insert_event(db, test_app.id, "a-uninstall", "uninstalled", start + timedelta(days=2))
    insert_event(db, beta.id, "b-install", "reinstalled", start + timedelta(days=3))
    insert_subscription(db, test_app.id, "a-sub", "a", Decimal("20"),
                        start + timedelta(days=1))
    insert_transaction(db, test_app.id, "a-cash", Decimal("19.40"),
                       start + timedelta(days=4))
    db.commit()

    report = build_period_report(
        db, [test_app, beta], start, end,
        start - (end - start), start, Scope.all(),
    )
    alpha = next(row for row in report.rows if row.app.slug == "test-app")
    assert (alpha.installs, alpha.uninstalls, alpha.net_installs) == (1, 1, 0)
    assert alpha.mrr_gained == Decimal("20")
    assert alpha.mrr_lost == Decimal("0")
    assert alpha.net_mrr == Decimal("20")
    assert alpha.collected == Decimal("19.40")
    assert report.totals.installs == sum(row.installs for row in report.rows)
    assert report.totals.net_mrr == sum(row.net_mrr for row in report.rows)
    assert report.totals.collected == sum(row.collected for row in report.rows)
```

- [ ] **Step 2: Run the report test and verify it fails**

Run: `uv run --frozen pytest tests/test_period_report.py -q`

Expected: FAIL during collection because `app_dashboard.period_report` is missing.

- [ ] **Step 3: Implement typed rows and totals**

```python
from dataclasses import dataclass
from decimal import Decimal

from app_dashboard.catalog import AppConfig


@dataclass(frozen=True)
class PeriodRow:
    app: AppConfig
    installs: int = 0
    uninstalls: int = 0
    mrr_gained: Decimal = Decimal("0")
    mrr_lost: Decimal = Decimal("0")
    net_mrr: Decimal = Decimal("0")
    collected: Decimal = Decimal("0")

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
    def comparison(self) -> dict[str, dict]:
        return {
            key: {"change": getattr(self.totals, key) -
                              getattr(self.previous_totals, key)}
            for key in PeriodTotals.__dataclass_fields__
        }
```

- [ ] **Step 4: Implement three grouped sources and report composition**

Add imports for `psycopg`, `Scope`, and `mrr_movements_by_app_between`, then implement the grouped sources and row builder:

```python
def _event_counts(conn, start, end, scope):
    predicate, params = scope.predicate("e")
    rows = conn.execute(
        f"""select e.app_id,
                   count(*) filter (where e.type in ('installed', 'reinstalled')),
                   count(*) filter (where e.type = 'uninstalled')
            from app_events e
            where e.occurred_at >= %s and e.occurred_at < %s and {predicate}
            group by e.app_id""",
        (start, end, *params),
    ).fetchall()
    return {app_id: (installs, uninstalls)
            for app_id, installs, uninstalls in rows}


def _cash_by_app(conn, start, end, scope):
    predicate, params = scope.predicate("t")
    rows = conn.execute(
        f"""select t.app_id, coalesce(sum(t.net_amount), 0)
            from transactions t
            where t.created_at >= %s and t.created_at < %s and {predicate}
            group by t.app_id""",
        (start, end, *params),
    ).fetchall()
    return dict(rows)


def _rows_between(conn, apps, start, end, scope):
    events = _event_counts(conn, start, end, scope)
    cash = _cash_by_app(conn, start, end, scope)
    movements = mrr_movements_by_app_between(conn, start, end, scope)
    included = (app for app in apps
                if scope.app_id is None or app.id == scope.app_id)
    rows = []
    for app in included:
        installs, uninstalls = events.get(app.id, (0, 0))
        movement = movements.get(app.id, {})
        gained = sum((movement.get(kind, Decimal("0"))
                      for kind in ("new", "reactivation", "expansion")),
                     Decimal("0"))
        lost = -sum((movement.get(kind, Decimal("0"))
                    for kind in ("contraction", "churned")), Decimal("0"))
        rows.append(PeriodRow(
            app=app, installs=installs, uninstalls=uninstalls,
            mrr_gained=gained, mrr_lost=lost,
            net_mrr=movement.get("net", Decimal("0")),
            collected=cash.get(app.id, Decimal("0")),
        ))
    return tuple(rows)
```

Build totals only from the finished rows:

```python
def _totals(rows: tuple[PeriodRow, ...]) -> PeriodTotals:
    return PeriodTotals(
        installs=sum(row.installs for row in rows),
        uninstalls=sum(row.uninstalls for row in rows),
        net_installs=sum(row.net_installs for row in rows),
        mrr_gained=sum((row.mrr_gained for row in rows), Decimal("0")),
        mrr_lost=sum((row.mrr_lost for row in rows), Decimal("0")),
        net_mrr=sum((row.net_mrr for row in rows), Decimal("0")),
        collected=sum((row.collected for row in rows), Decimal("0")),
    )


def build_period_report(conn, apps, start, end, previous_start, previous_end,
                        scope=Scope.all()) -> PeriodReport:
    current = _rows_between(conn, apps, start, end, scope)
    previous = _rows_between(conn, apps, previous_start, previous_end, scope)
    return PeriodReport(current, _totals(current), _totals(previous))
```

Add allowlisted, stable server-side sorting. Invalid keys and directions intentionally fall back to the default rather than raising:

```python
SORT_KEYS = {
    "app": lambda row: row.app.name.casefold(),
    "installs": lambda row: row.installs,
    "uninstalls": lambda row: row.uninstalls,
    "net_installs": lambda row: row.net_installs,
    "mrr_gained": lambda row: row.mrr_gained,
    "mrr_lost": lambda row: row.mrr_lost,
    "net_mrr": lambda row: row.net_mrr,
    "collected": lambda row: row.collected,
}


def sort_rows(rows, key="net_mrr", direction="desc"):
    key = key if key in SORT_KEYS else "net_mrr"
    direction = direction if direction in {"asc", "desc"} else "desc"
    alphabetical = sorted(rows, key=lambda row: row.app.name.casefold())
    return tuple(sorted(alphabetical, key=SORT_KEYS[key],
                        reverse=direction == "desc"))
```

- [ ] **Step 5: Add tests for free/trial/annual handling, negative cash, scope, and sorting**

Add cases that assert:

```python
assert annual_row.mrr_gained == Decimal("5.00")   # $60 annual / 12
assert trial_row.mrr_gained == Decimal("0")
assert refund_row.collected == Decimal("-9.70")
assert {row.app.slug for row in scoped.rows} == {"beta"}
assert [row.app.slug for row in sort_rows(report.rows, "net_mrr", "desc")][:2] \
       == ["winner", "flat"]
```

The trial fixture must insert a future `trial_ends_at` in `active_subscriptions`; the annual fixture must use an app whose `annual_plan_amounts` contains `60.00` and a charge with `plan_interval='ANNUAL'`.

- [ ] **Step 6: Run report tests**

Run: `uv run --frozen pytest tests/test_period_report.py tests/test_stats.py -q`

Expected: all tests PASS.

- [ ] **Step 7: Commit the report service**

```bash
git add src/app_dashboard/period_report.py tests/test_period_report.py
git commit -m "Aggregate portfolio performance over arbitrary periods"
```

### Task 4: Add metric definitions and the authenticated route

**Files:**
- Modify: `src/app_dashboard/metrics.py`
- Modify: `src/app_dashboard/web.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing route and metric-definition tests**

```python
def test_period_page_is_authenticated_and_defaults_to_30_days(db):
    app = create_app(conn_factory=lambda: keep_open(db))
    anonymous = dashboard_client(app, authenticated=False)
    assert anonymous.get("/period", headers={"Accept": "text/html"},
                         follow_redirects=False).status_code == 307
    page = dashboard_client(app).get("/period")
    assert page.status_code == 200
    assert "Period" in page.text
    assert 'aria-current="page"' in page.text
    assert 'href="/period?period=30d"' in page.text


def test_period_page_keeps_app_and_custom_boundaries(db):
    page = dashboard_client(create_app(conn_factory=lambda: keep_open(db))).get(
        "/period?app=test-app&period=custom&start=2026-08-01&end=2026-08-10"
    )
    assert page.status_code == 200
    assert 'name="app" value="test-app"' in page.text
    assert 'name="start" value="2026-08-01"' in page.text
    assert 'name="end" value="2026-08-10"' in page.text
```

- [ ] **Step 2: Run the route tests and verify 404 failures**

Run: `uv run --frozen pytest tests/test_web.py -k 'period_page' -q`

Expected: FAIL because `/period` is not registered.

- [ ] **Step 3: Register seven generic period metrics**

Add keys `period_installs`, `period_uninstalls`, `period_net_installs`, `period_mrr_gained`, `period_mrr_lost`, `period_net_mrr`, and `period_collected` to `METRICS`. Use `kind="window"`; use `unit="usd"` for the four money values; set `better="down"` for uninstalls and MRR lost, `better="up"` for installs, net installs, MRR gained, net MRR, and collected. Definitions must say “selected period” rather than hard-code 30 days.

- [ ] **Step 4: Add the route as a thin orchestration layer**

Import `resolve_period`, `PRESET_LABELS`, `build_period_report`, and `sort_rows`. Add:

```python
    @app.get("/period")
    def period_report_page(request: Request,
                           user: str = Depends(verify_creds)):
        selected = resolve_period(
            request.query_params.get("period"),
            request.query_params.get("start"),
            request.query_params.get("end"),
        )
        sort_key = request.query_params.get("sort", "net_mrr")
        direction = request.query_params.get("direction", "desc")
        conn = conn_factory()
        try:
            scope, selected_app, apps = resolve_scope(request, conn)
            report = build_period_report(
                conn, apps, selected.start, selected.end,
                selected.previous_start, selected.previous_end, scope,
            )
        finally:
            conn.close()
        rows = sort_rows(report.rows, sort_key, direction)
        retained = urlencode(selected.query_items())
        return templates.TemplateResponse(
            request, "period.html",
            {**page_context(request, user, "period", selected_app, apps),
             "period": selected, "report": report, "rows": rows,
             "preset_labels": PRESET_LABELS, "period_qs": retained,
             "sort_key": sort_key, "direction": direction},
        )
```

Before rendering, build preset and sort URLs with `urllib.parse.urlencode`; do not construct query strings inside Jinja from untrusted input. Ensure sort allowlisting happens in `sort_rows`, falling back to `net_mrr desc` rather than raising.

- [ ] **Step 5: Run web tests**

Run: `uv run --frozen pytest tests/test_web.py -k 'period_page' -q`

Expected: tests now reach the missing template and fail with `TemplateNotFound: period.html`; this proves authentication, resolution, and service orchestration execute.

- [ ] **Step 6: Commit route and definitions**

```bash
git add src/app_dashboard/metrics.py src/app_dashboard/web.py tests/test_web.py
git commit -m "Expose the portfolio period report route"
```

### Task 5: Render the responsive report

**Files:**
- Create: `src/app_dashboard/templates/period.html`
- Modify: `src/app_dashboard/templates/_macros.html`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Extend the shared stat macro with a dynamic comparison label**

Change the signature and label line without changing Overview callers:

```jinja2
{% macro stat(key, value, tone="", extra="", compare=none, compare_label=none) %}
...
<span class="delta-when">{{ compare_label or COMPARE_LABEL[m.kind] }}</span>
```

- [ ] **Step 2: Add Period directly below Overview in the nav**

Add this NAV row before Activity:

```jinja2
("/period", "period", "Period",
 '<rect x="3" y="4" width="14" height="13" rx="2"/>'
 '<path d="M3 8h14M7 2.8v3M13 2.8v3"/>',
 []),
```

- [ ] **Step 3: Create the page with presets, custom dates, cards, and table**

The template must:

1. Render preset links from route-prepared URLs in a `.period-presets` segmented control.
2. Render a GET custom form with `period=custom`, retained `app`, two `data-datepicker` date fields, and one submit button.
3. Show `period.error` in `.sync-note`.
4. Show the resolved inclusive local dates and “Compared with the preceding period of the same length.”
5. Render seven `ui.stat` cards using `compare_label="vs previous period"`.
6. Render `This period has not started yet.` when `period.is_future`.
7. Otherwise render a `.table-card` with server-generated sortable header links and every row in `rows`.
8. Link app names to `/period` with the same period query and that app's slug.

The money cells use two decimals and signed color only for `net_mrr` and `collected`. `mrr_lost` is displayed as a positive magnitude because the column label already carries the loss meaning.

- [ ] **Step 4: Add narrow, domain-specific CSS**

```css
.period-toolbar { display: flex; align-items: end; justify-content: space-between;
                  gap: 12px; flex-wrap: wrap; }
.period-presets { display: inline-flex; gap: 3px; padding: 3px;
                  border: 1px solid var(--control-border); border-radius: 9px;
                  background: var(--control-inset); }
.period-presets a { min-height: 36px; padding: 7px 11px; display: inline-flex;
                    align-items: center; border-radius: 7px; text-decoration: none;
                    color: var(--ink-2); font-weight: 600; font-size: 13px; }
.period-presets a[aria-current="true"] { color: var(--on-brand);
                                          background: var(--brand); }
.period-cards { grid-template-columns: repeat(7, minmax(140px, 1fr)); }
.period-table { min-width: 980px; }
@media (max-width: 900px) {
  .period-presets { width: 100%; overflow-x: auto; }
  .period-presets a { white-space: nowrap; }
  .period-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 520px) {
  .period-cards { grid-template-columns: minmax(0, 1fr); }
}
```

- [ ] **Step 5: Complete HTML assertions**

Assert the page contains all seven metric names, `vs previous period` seven times, every preset, the selected app row link with retained period params, sortable links, `When (CET/CEST)` or an equivalent explicit timezone label beside the resolved dates, and the future/empty messages in their respective fixtures.

- [ ] **Step 6: Run all focused tests**

Run: `uv run --frozen pytest tests/test_periods.py tests/test_period_report.py tests/test_stats.py tests/test_web.py -q`

Expected: all focused tests PASS.

- [ ] **Step 7: Commit the report UI**

```bash
git add src/app_dashboard/templates/period.html \
  src/app_dashboard/templates/_macros.html \
  src/app_dashboard/templates/base.html tests/test_web.py
git commit -m "Show app performance over selectable periods"
```

### Task 6: Verify the complete product path

**Files:**
- Modify only if verification finds a defect in the files listed above.

- [ ] **Step 1: Run formatting and the complete suite**

Run:

```bash
git diff --check
uv run --frozen pytest -q
```

Expected: no whitespace errors and all tests PASS. The known Starlette `httpx` deprecation warning may remain; no new warnings are acceptable.

- [ ] **Step 2: Start the production-shaped local server**

Run the repository's documented Uvicorn command with `NO_SCHEDULER=1` and the existing local dashboard credentials. Use the next free port if 8000 is occupied.

Expected: startup completes and `/auth/login`, `/period`, and Flatpickr static assets return successfully.

- [ ] **Step 3: Verify desktop behavior with Playwright**

At `1440x900`, verify:

- the default 30-day preset is selected;
- all seven totals and every active app row render;
- sorting Net MRR both directions changes row order without losing the period;
- clicking an app keeps the period and scopes the report;
- custom date inputs open the styled picker;
- invalid and future custom ranges show their explicit messages;
- browser console contains no errors.

- [ ] **Step 4: Verify mobile behavior with Playwright**

At `390x844`, verify:

- preset controls can be reached without widening the document;
- custom fields and button form one readable column;
- summary cards do not clip money values;
- only the table card scrolls horizontally;
- the document's `scrollWidth` equals its viewport content width;
- switching apps through the bottom sheet retains period parameters.

- [ ] **Step 5: Review the final diff against the design**

Check each requirement in `docs/plans/2026-08-11-period-report-design.md` against an implementation or test. Confirm `git status --short` lists only intentional files and no Playwright artifacts.

- [ ] **Step 6: Commit any verification fixes**

If verification required changes, stage only those files and commit:

```bash
git add src/app_dashboard tests
git commit -m "Correct period report integration details"
```

If no files changed, do not create an empty commit.
