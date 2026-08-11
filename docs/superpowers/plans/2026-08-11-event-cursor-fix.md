# Event Cursor Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every incremental Partner event poll see newly added events while retaining overlap and idempotent replay.

**Architecture:** Start every event connection at its newest page and use `occurredAtMin` as the cross-run high-water boundary. Keep GraphQL cursors local to one pagination loop and use an unbounded time range only for explicit full replay.

**Tech Stack:** Python 3.13, Shopify Partner GraphQL API, psycopg, pytest

---

### Task 1: Lock down the GraphQL time boundary

**Files:**
- Modify: `tests/test_partner_api.py`
- Modify: `src/app_dashboard/partner_api.py`

- [ ] Add a failing test that calls `fetch_app_events(..., occurred_at_min="2026-08-11T07:00:00+00:00")` and asserts the serialized GraphQL variables contain `occurredAtMin`.
- [ ] Run `uv run --frozen pytest tests/test_partner_api.py -q` and confirm the new argument fails.
- [ ] Add `$occurredAtMin: DateTime` to `_APP_EVENTS_QUERY`, pass it to `events`, and include it in request variables.
- [ ] Rerun the Partner API tests and confirm they pass.

### Task 2: Replace the persisted event cursor with overlap polling

**Files:**
- Modify: `tests/test_pipeline.py`
- Modify: `src/app_dashboard/pipeline.py`

- [ ] Replace the saved-cursor expectation with a regression test that seeds a stale cursor and `last_synced_at`, then asserts a normal poll starts at `after_cursor=None` and passes `last_synced_at - poll_overlap_minutes` as `occurred_at_min`.
- [ ] Add a test that full history passes no time boundary.
- [ ] Run `uv run --frozen pytest tests/test_pipeline.py -q` and confirm the regression fails against the current implementation.
- [ ] Make `run_sync` ignore stored cursors, compute the incremental UTC ISO boundary, keep cursor changes inside the page loop, and persist `cursor=null` after success.
- [ ] Rerun pipeline and scheduler tests.

### Task 3: Correct documentation and recover live data

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/partner-api-notes.md`
- Modify: `docs/deploy.md`

- [ ] Document that event cursors are pagination-only and that incremental polling uses a time overlap.
- [ ] Run `git diff --check` and `uv run --frozen pytest -q`.
- [ ] Restart the local server, run full lifecycle sync for every active app, and wait for completion.
- [ ] Query Aeris' raw and derived events and verify the August 11 install and subscription with $24.99 MRR.
- [ ] Run `DATABASE_URL=postgresql:///app_dashboard_multi_real uv run --frozen python scripts/check_invariants.py`.
- [ ] Verify the Activity page in the local browser and commit the fix.
