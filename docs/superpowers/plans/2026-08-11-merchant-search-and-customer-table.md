# Merchant Search And Customer Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add scoped merchant search to Overview and expose trustworthy plan, MRR, status, installation, and latest-event data on Customers.

**Architecture:** Enrich the existing paginated customer query with lateral lookups for the current subscription, active trial, and latest lifecycle event. Reuse that query in a small HTMX search endpoint whose fragment links directly to merchant detail, while normal form submission opens the full Customers results.

**Tech Stack:** Python 3.13, FastAPI, PostgreSQL/psycopg, Jinja2, HTMX, pytest

---

### Task 1: Enrich Customer Rows

**Files:**
- Modify: `src/app_dashboard/customers.py`
- Test: `tests/test_customers.py`

- [ ] **Step 1: Write failing query tests**

Add fixtures for a live monthly subscriber, active trial, free subscriber,
cancelled subscriber, and uninstalled shop. Assert that `list_customers()`
returns `plan_label`, `mrr`, `customer_status`, `latest_event_type`, and
`latest_event_at`, with trial/free/uninstalled rows excluded from MRR.

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
uv run --frozen pytest tests/test_customers.py -q
```

Expected: failures because the new row fields do not exist.

- [ ] **Step 3: Add bounded SQL enrichment and row derivation**

Extend `list_customers()` with lateral joins that select one live subscription,
one future trial, and one latest event per already-filtered shop row. Add a small
row decorator with this precedence:

```python
if row["install_state"] == "uninstalled":
    status = "Uninstalled"
elif row["trial_ends_at"] is not None:
    status = "Trial"
elif row["monthly_amount"] is not None and row["monthly_amount"] > 0:
    status = "Paying"
elif row["monthly_amount"] is not None:
    status = "Free"
elif row["latest_event_type"] == "unsubscribed":
    status = "Cancelled"
else:
    status = "Installed"
```

Derive `plan_label` as Trial, Free, Annual, Monthly, or `None`; derive MRR only
for installed, non-trial, positive live subscriptions.

- [ ] **Step 4: Verify customer query tests pass**

Run:

```bash
uv run --frozen pytest tests/test_customers.py -q
```

Expected: all customer tests pass.

### Task 2: Add Overview Merchant Search

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/overview.html`
- Create: `src/app_dashboard/templates/_merchant_search_results.html`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write failing endpoint and rendering tests**

Assert that `/customer-search?search=suzy` returns matching merchants, respects
`?app=<slug>`, caps visible result rows at eight, preserves scoped detail links,
and renders no list for blank input. Assert Overview contains the search control.

- [ ] **Step 2: Verify the focused tests fail**

Run:

```bash
uv run --frozen pytest tests/test_web.py -q -k 'merchant_search or overview_contains_search'
```

Expected: 404 or missing-element failures.

- [ ] **Step 3: Implement the read-only search endpoint**

Add `GET /customer-search`. Resolve the normal app scope, trim the input, call
`list_customers(..., limit=9)` only for non-blank input, render the first eight
rows, and use the ninth row only to set `has_more`. Build the full Customers URL
with `urllib.parse.urlencode`.

- [ ] **Step 4: Render the Overview form and result fragment**

Add a search form below `.ops-strip`. The input requests the fragment after a
250 ms HTMX delay and includes the selected app. Normal Enter/button submission
uses `GET /customers`. Render merchant, domain, app, state, and direct detail
links in a normal-flow result list.

- [ ] **Step 5: Add responsive styles**

Give the form a stable input/button layout, wrap it below 620px, stack result
metadata on mobile, and keep all widths bounded by the main content area.

- [ ] **Step 6: Verify search tests pass**

Run:

```bash
uv run --frozen pytest tests/test_web.py -q -k 'merchant_search or overview_contains_search'
```

Expected: all selected tests pass.

### Task 3: Upgrade The Customers Table

**Files:**
- Modify: `src/app_dashboard/templates/customers.html`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write a failing table contract test**

Create representative subscription and event data, request `/customers`, and
assert the response contains the new headings and truthful Trial, Free, Paying,
Cancelled, and Uninstalled values.

- [ ] **Step 2: Verify the table test fails**

Run:

```bash
uv run --frozen pytest tests/test_web.py -q -k customer_table_shows_current_commercial_state
```

Expected: failure because the old identity columns still render.

- [ ] **Step 3: Replace the table columns**

Render Merchant, App, Plan, MRR, Status, Installed, and Latest event. Keep the
domain under the merchant name and link the name to scoped merchant detail.
Use pills for state, muted dashes for unavailable data, and normalized monthly
MRR rather than annual sticker price.

- [ ] **Step 4: Verify focused and full tests**

Run:

```bash
uv run --frozen pytest tests/test_customers.py tests/test_web.py -q
uv run --frozen pytest -q
git diff --check
```

Expected: all tests pass and the diff has no whitespace errors.

- [ ] **Step 5: Verify the running UI**

Restart the local Uvicorn server, search for a known merchant on Overview, open
the result, and inspect Customers at desktop and 390x844 viewport sizes. Confirm
the search list is usable, table scroll is contained, and page-level horizontal
overflow is absent.

- [ ] **Step 6: Commit the implementation**

```bash
git add src/app_dashboard tests
git commit -m "Add merchant search and richer customer status"
```
