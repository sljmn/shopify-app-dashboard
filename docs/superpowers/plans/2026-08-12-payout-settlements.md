# Payout Settlements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an accurate Shopify payout settlement ledger with date and net amount.

**Architecture:** Pull `Earning` objects from the Partner API historical events feed into a dedicated table, independently from lifecycle and transaction data. Build a scoped reporting module and render grouped settlement dates with drill-down rows.

**Tech Stack:** Python 3.13, FastAPI, PostgreSQL/psycopg, Jinja2, pytest, Shopify Partner GraphQL API 2026-07.

---

### Task 1: Persist and fetch earnings

**Files:**
- Create: `src/app_dashboard/migrations/026_payout_earnings.sql`
- Modify: `src/app_dashboard/partner_api.py`
- Modify: `src/app_dashboard/ingest_raw.py`
- Test: `tests/test_partner_api.py`
- Test: `tests/test_ingest_raw.py`

- [ ] Add the `payout_earnings` table keyed by app and Partner event id.
- [ ] Add a paginated `fetch_earnings` query filtered to app earning event types.
- [ ] Map `settlementDate`, `netAmount`, `grossAmount`, Shopify fee and shop identity.
- [ ] Upsert mutable settlement fields and verify repeated rows remain idempotent.

### Task 2: Synchronize earnings independently

**Files:**
- Modify: `src/app_dashboard/pipeline.py`
- Modify: `src/app_dashboard/scheduler.py`
- Modify: `src/app_dashboard/manual_sync.py`
- Test: `tests/test_pipeline.py`
- Test: `tests/test_scheduler.py`
- Test: `tests/test_manual_sync.py`

- [ ] Split the initial/full range into Partner API windows of at most 365 days.
- [ ] Add overlapping incremental polling with independent `sync_state` progress.
- [ ] Register the scheduled job and include payouts in manual fresh/full refreshes.
- [ ] Verify pagination, range boundaries, connection cleanup and failure isolation.

### Task 3: Report and render payouts

**Files:**
- Create: `src/app_dashboard/payouts.py`
- Create: `src/app_dashboard/templates/payouts.html`
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_payouts.py`
- Test: `tests/test_web.py`

- [ ] Aggregate net amounts by settlement date and currency inside the selected app scope.
- [ ] Return detail rows when a settlement date is selected.
- [ ] Add authenticated `/payouts` routing, date validation and preserved app scope.
- [ ] Add the sidebar entry and responsive date/amount table.
- [ ] Run focused tests, then the full suite.
