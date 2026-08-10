# Active Trials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exclude free plans and live trials from paying metrics and provide a dedicated, app-scoped Trials page backed by Shopify's current active-subscription state.

**Architecture:** Persist one Partner API `activeSubscription` snapshot per app installation, refreshed independently from lifecycle and transaction feeds. Centralize the billable-subscription SQL predicate so overview, historical MRR, plan mix, and paying cohorts agree. Read the same snapshots into a dedicated Trials report and merchant detail.

**Tech Stack:** Python 3.13, FastAPI, psycopg/Postgres, APScheduler, Jinja2, Shopify Partner GraphQL API 2026-07, pytest.

---

## File Map

- `src/app_dashboard/migrations/012_active_subscriptions.sql`: current subscription snapshots and indexes.
- `src/app_dashboard/partner_api.py`: active-subscription GraphQL query and response mapping.
- `src/app_dashboard/active_subscriptions.py`: snapshot upsert/delete and full per-app refresh.
- `src/app_dashboard/scheduler.py`: independent six-hour refresh job.
- `src/app_dashboard/stats.py`: shared free/trial exclusion across paying metrics.
- `src/app_dashboard/trials.py`: scoped current-trial summary and rows.
- `src/app_dashboard/customers.py`: current trial context on merchant detail.
- `src/app_dashboard/web.py`: `/trials` route and template context.
- `src/app_dashboard/templates/base.html`: Trials navigation item.
- `src/app_dashboard/templates/trials.html`: trial summary and merchant table.
- `src/app_dashboard/templates/customer.html`: explicit live-trial state.
- `src/app_dashboard/templates/actions.html`: rename the non-subscriber proxy.
- `src/app_dashboard/metrics.py`: corrected metric definitions.
- `tests/test_partner_api.py`, `tests/test_active_subscriptions.py`, `tests/test_scheduler.py`, `tests/test_stats.py`, `tests/test_web.py`, `tests/test_migrations.py`: regression coverage.

### Task 1: Persist Current Active Subscriptions

**Files:**
- Create: `src/app_dashboard/migrations/012_active_subscriptions.sql`
- Modify: `tests/test_migrations.py`

- [x] **Step 1: Add the failing migration assertions**

Assert `active_subscriptions` exists, has a required `app_id`, and accepts one
row per `(app_id, shop_gid)` while rejecting duplicate snapshots for that pair.

- [x] **Step 2: Run the migration tests**

```bash
.venv/bin/pytest tests/test_migrations.py -q
```

Expected: failure because the table does not exist.

- [x] **Step 3: Add migration 012**

Create the table with this ownership boundary:

```sql
create table active_subscriptions (
    app_id bigint not null,
    shop_gid text not null,
    legacy_subscription_id text,
    billing_period text,
    trial_ends_at timestamptz,
    cancel_at_end_of_cycle boolean not null default false,
    item_handle text,
    item_description text,
    currency_code text,
    payload jsonb not null default '{}'::jsonb,
    observed_at timestamptz not null,
    primary key (app_id, shop_gid),
    foreign key (app_id, shop_gid) references shops(app_id, shop_gid)
        on delete cascade
);
```

Add indexes on `trial_ends_at` and `legacy_subscription_id` scoped by app.

- [x] **Step 4: Run the migration tests and commit**

```bash
.venv/bin/pytest tests/test_migrations.py -q
git add src/app_dashboard/migrations/012_active_subscriptions.sql tests/test_migrations.py
git commit -m "Store current Shopify subscription snapshots"
```

### Task 2: Fetch And Refresh Active Subscriptions

**Files:**
- Modify: `src/app_dashboard/partner_api.py`
- Create: `src/app_dashboard/active_subscriptions.py`
- Modify: `src/app_dashboard/scheduler.py`
- Modify: `tests/test_partner_api.py`
- Create: `tests/test_active_subscriptions.py`
- Modify: `tests/test_scheduler.py`

- [x] **Step 1: Add failing Partner API mapping tests**

Cover Shopify-GID conversion, a populated snapshot, `nil` subscription, and
GraphQL errors. The mapped result must expose `trial_ends_at`,
`cancel_at_end_of_cycle`, `legacy_subscription_id`, plan metadata, and payload.

- [x] **Step 2: Add failing refresh tests**

Insert installed shops, return one active snapshot and one `nil`, and prove the
refresh upserts the first, deletes the stale second, records its own sync-state
source, and does not query uninstalled shops.

- [x] **Step 3: Implement query and refresh**

Add `fetch_active_subscription(client, *, app_id, shop_id) -> dict | None`.
Implement `sync_active_subscriptions(conn, client, app, sleep=time.sleep)` with
bound SQL parameters, sequential requests, and the existing throttle.

- [x] **Step 4: Register an independent six-hour scheduler job**

Use the existing `run_all_apps` isolation wrapper. Delay the first scheduled
run five minutes so boot-time lifecycle and transaction refreshes finish first.

- [x] **Step 5: Run focused tests and commit**

```bash
.venv/bin/pytest tests/test_partner_api.py tests/test_active_subscriptions.py tests/test_scheduler.py -q
git add src/app_dashboard/partner_api.py src/app_dashboard/active_subscriptions.py \
  src/app_dashboard/scheduler.py tests/test_partner_api.py \
  tests/test_active_subscriptions.py tests/test_scheduler.py
git commit -m "Sync current subscriptions independently"
```

### Task 3: Correct Paying Metrics

**Files:**
- Modify: `src/app_dashboard/stats.py`
- Modify: `src/app_dashboard/metrics.py`
- Modify: `tests/test_stats.py`

- [x] **Step 1: Add failing metric tests**

Create one paid shop, one free shop, one current trial, and one expired trial.
Assert only paid and expired-trial shops contribute to MRR and paying count.
Pin the same rule for plan mix and MRR trend so headline and charts agree.

- [x] **Step 2: Implement one shared billable predicate**

Require `monthly_amount > 0.01` and exclude any matching snapshot with
`trial_ends_at > now()`. Reuse this predicate in current overview metrics,
historical MRR inputs, plan mix, and paying action cohorts.

- [x] **Step 3: Update metric definitions**

State that paying shops and MRR exclude free plans and current Shopify trials,
and cite `active_subscriptions` as a source.

- [x] **Step 4: Run focused tests and commit**

```bash
.venv/bin/pytest tests/test_stats.py tests/test_web.py -q
git add src/app_dashboard/stats.py src/app_dashboard/metrics.py tests/test_stats.py
git commit -m "Exclude free plans and active trials from MRR"
```

### Task 4: Build The Scoped Trials Experience

**Files:**
- Create: `src/app_dashboard/trials.py`
- Modify: `src/app_dashboard/customers.py`
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/base.html`
- Create: `src/app_dashboard/templates/trials.html`
- Modify: `src/app_dashboard/templates/customer.html`
- Modify: `src/app_dashboard/templates/actions.html`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Add failing route and scope tests**

Assert `/trials` requires auth, renders current trials only, isolates selected
apps, preserves app scope in merchant links, separates converting and
cancel-scheduled MRR, and shows a not-yet-synced state when no snapshots exist.

- [ ] **Step 2: Implement the report query**

Join current trial snapshots to apps, shops, and the matching derived
subscription. Return summary totals and rows ordered by earliest trial end.

- [ ] **Step 3: Add route, navigation, and template**

Add `Trials` after Customers in the sidebar. Render compact summary metrics and
one table containing end date, app, merchant, storefront, plan, MRR, and status.

- [ ] **Step 4: Expose trial status on merchant detail**

For a current snapshot with a future `trial_ends_at`, label the plan card
`Trial` and show its end date and potential monthly value.

- [ ] **Step 5: Rename the Actions proxy and run web tests**

Rename visible "Trial watch" copy to "New installs without a subscription".

```bash
.venv/bin/pytest tests/test_web.py tests/test_customers.py -q
git add src/app_dashboard/trials.py src/app_dashboard/customers.py \
  src/app_dashboard/web.py src/app_dashboard/templates/base.html \
  src/app_dashboard/templates/trials.html src/app_dashboard/templates/customer.html \
  src/app_dashboard/templates/actions.html tests/test_web.py
git commit -m "Add an app-scoped current trials workspace"
```

### Task 5: Backfill And Verify Live Data

**Files:**
- Modify if needed: `scripts/check_invariants.py`

- [ ] **Step 1: Apply migration 012 to the live local database**

```bash
DATABASE_URL=postgresql://localhost:5432/app_dashboard_multi_real \
  .venv/bin/python -m app_dashboard.migrate
```

- [ ] **Step 2: Run the active-subscription backfill for all configured apps**

Execute the same scheduler function once and report per-app queried, stored,
removed, trial, and failure counts without printing tokens or raw payloads.

- [ ] **Step 3: Reconcile headline numbers**

Compare MRR, paying shops, active installations, and ARPU against Mantle. Explain
any remaining row-level difference; do not declare parity from rounded totals.

- [ ] **Step 4: Run complete verification**

```bash
.venv/bin/pytest -q
DATABASE_URL=postgresql://localhost:5432/app_dashboard_multi_real \
  .venv/bin/python scripts/check_invariants.py
```

Expected: all tests and invariants pass.

- [ ] **Step 5: Browser smoke test and final commit**

Verify All apps, one selected app, Trials, a trial merchant, free plan display,
internal merchant links, and storefront links on the running local server.
