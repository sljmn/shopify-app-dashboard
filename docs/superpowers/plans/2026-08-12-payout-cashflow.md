# Payout Cashflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ledger-first Payouts page with a Mantle-style cashflow overview while retaining the exact settlement ledger as its detail view.

**Architecture:** Extend `payouts.py` with payout-window presentation models built from authoritative earning rows. Completed and pending windows use Shopify earning data; only the next empty window uses a recurring-value projection and is explicitly marked Estimated. Render the overview with lightweight HTML/CSS bars and keep the existing table below it.

**Tech Stack:** Python, psycopg, FastAPI/Jinja, server-rendered HTML/CSS, pytest.

---

### Task 1: Payout windows and statuses

**Files:**
- Modify: `src/app_dashboard/payouts.py`
- Test: `tests/test_payouts.py`

- [x] Add tests covering half-month window assignment, expected 6th/20th payment dates, authoritative versus estimated status, currency separation, and annual billing cadence.
- [x] Run `pytest tests/test_payouts.py -q` while implementing the payout model.
- [x] Add immutable payout-window models and build them from `occurred_at`, `settlement_date`, and `net_amount` without changing settled totals.
- [x] Project only charges due in the future window from current monthly and annual subscription cadence and mark the result `estimated=True`.
- [x] Run `pytest tests/test_payouts.py -q` and confirm all payout tests pass.

### Task 2: Mantle-style overview

**Files:**
- Modify: `src/app_dashboard/templates/payouts.html`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [x] Add page assertions for the cashflow panel, status legend, period rows, Estimated label, and `View details` anchor.
- [x] Run `pytest tests/test_web.py -q -k payouts` while implementing the page.
- [x] Render three recent/next payout windows as a responsive stacked bar chart with a compact period list.
- [x] Move date filtering and settlement history below `#details`, reached through `View details`.
- [x] Add mobile CSS that preserves full-width chart labels and rows without horizontal scrolling.
- [x] Run `pytest tests/test_web.py -q -k payouts` and confirm all payout page tests pass.

### Task 3: Regression verification

**Files:**
- Verify only.

- [x] Run `pytest tests/test_payouts.py tests/test_web.py tests/test_partner_api.py tests/test_pipeline.py -q` (153 passed).
- [ ] Inspect the deployed `/payouts` at desktop and mobile widths.
- [ ] Commit and deploy the implementation with a message explaining why the cashflow view replaced the ledger-first presentation.
