# Lifecycle Data Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct lifecycle derivation and make current and historical MRR detectably agree with Shopify state.

**Architecture:** Rebuild a canonical clean movement ledger from immutable raw events while retaining `subscriptions` as current materialized state. Historical reports consume clean movement deltas; validators compare that independent path with current state and Shopify snapshots.

**Tech Stack:** Python 3.13, PostgreSQL, psycopg 3, pytest, FastAPI/Jinja.

---

### Task 1: Correct lifecycle classification and replay cleanup

**Files:**
- Modify: `src/app_dashboard/derive.py`
- Test: `tests/test_derive.py`

- [ ] **Step 1: Add failing lifecycle tests**

Add focused tests proving that a replacement activation emits only the net delta, its correlated cancellation emits no clean churn row, a later activation after true churn is `resubscribed`, freeze/unfreeze removes and restores MRR, and declined/expired events become `charge_abandoned`.

- [ ] **Step 2: Run the focused tests and confirm the old behavior fails**

Run: `pytest tests/test_derive.py -q`

Expected: the new tests fail on unsupported event types and old plan-change classification.

- [ ] **Step 3: Implement deterministic replay classification**

Precompute correlated cancellations from the installation's ordered raw history. Carry current subscription and normalized MRR, close a replaced subscription at activation time, emit net plan-change deltas, distinguish `resubscribed`, and add frozen/unfrozen/abandoned clean types.

- [ ] **Step 4: Make derived rows replayable**

Update deterministic fields on conflict and delete previously-derived rows for raw events suppressed by the corrected replay. Keep raw events untouched.

- [ ] **Step 5: Run focused derivation tests**

Run: `pytest tests/test_derive.py tests/test_pipeline.py tests/test_invariants.py -q`

Expected: all selected tests pass.

### Task 2: Reconstruct historical MRR from the clean ledger

**Files:**
- Modify: `src/app_dashboard/stats.py`
- Modify: `src/app_dashboard/metrics.py`
- Modify: `docs/architecture.md`
- Test: `tests/test_stats.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: Add failing historical reconstruction tests**

Cover a freeze/unfreeze gap, a net upgrade, real churn, resubscription, and equality between the final event-ledger value and current subscription MRR.

- [ ] **Step 2: Run focused report tests and confirm failure**

Run: `pytest tests/test_stats.py tests/test_metrics.py -q`

- [ ] **Step 3: Change historical MRR queries to cumulative clean deltas**

Build the month-end series from all clean events before each boundary. Keep current overview MRR on the live-subscription query so the two paths remain independent.

- [ ] **Step 4: Base churn and LTV on genuine lifecycle movement**

Count real `unsubscribed`/freeze loss and subtract `resubscribed`/unfreeze returns rather than counting every closed subscription row, which includes replaced plan ids.

- [ ] **Step 5: Update metric definitions and architecture documentation**

Document which representation owns current state, historical movement, and validation.

- [ ] **Step 6: Run report tests**

Run: `pytest tests/test_stats.py tests/test_metrics.py tests/test_web.py -q`

Expected: all selected tests pass.

### Task 3: Add independent live-data validators

**Files:**
- Modify: `scripts/check_invariants.py`
- Test: `tests/test_invariants.py`

- [ ] **Step 1: Add failing validator tests**

Seed an ignored unfreeze, a current Shopify snapshot without a derived live subscription, and a correlated cancellation left as churn. Assert that each produces a named failure.

- [ ] **Step 2: Implement validator queries**

Compare current state MRR to cumulative ledger MRR per app, compare active snapshot ids to derived live ids, verify supported raw event coverage, and reject correlated cancellations that survived derivation.

- [ ] **Step 3: Run invariant tests**

Run: `pytest tests/test_invariants.py -q`

Expected: all tests pass.

### Task 4: Verify, replay, and measure real data

**Files:**
- Modify only if a verified defect is found in Tasks 1-3.

- [ ] **Step 1: Run the full automated suite**

Run: `pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Capture the production-copy baseline**

Record per-app current MRR, clean event counts, raw lifecycle counts, current snapshot mismatches, and correlated cancellation counts from `app_dashboard_multi_real`.

- [ ] **Step 3: Replay full history for every configured app**

Use the existing full-history/manual-sync path so raw ingestion and derivation remain idempotent. Do not mutate source raw events.

- [ ] **Step 4: Run live invariants and compare results**

Run: `DATABASE_URL=postgresql:///app_dashboard_multi_real python scripts/check_invariants.py`

Expected: all invariants pass, snapshot mismatch is zero for supported paid subscriptions, the two known unfrozen subscriptions contribute `$34.16` MRR, and correlated cancellation clean rows are absent.

- [ ] **Step 5: Smoke-test the running dashboard**

Check Overview, Activity, Customers, merchant detail, Churn, Retention, and Trials at desktop and mobile widths. Verify no overlap, correct labels, working merchant links, and unchanged trial exclusion.

- [ ] **Step 6: Commit the implementation**

Stage only lifecycle-correctness code, tests, and documentation. Commit with a message describing why lifecycle reconstruction changed and what was corrected.
