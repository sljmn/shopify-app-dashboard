# Merchant Plan And Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clearly distinguish free merchants from paid merchants and add internal detail plus external storefront links to Latest activity.

**Architecture:** Keep billing classification in the existing merchant detail presentation because `monthly_amount` already carries the normalized recurring value. Extend the existing recent-events result with shop identity and domain so the Overview template can construct both links without another query or new persistence.

**Tech Stack:** Python 3.13, FastAPI, psycopg/Postgres, Jinja2, pytest.

---

## File Map

- `src/app_dashboard/customers.py`: continues to supply the active subscription value used by merchant detail.
- `src/app_dashboard/stats.py`: includes `shop_gid` and `shop_domain` in recent activity rows.
- `src/app_dashboard/templates/customer.html`: labels zero-value active subscriptions as free.
- `src/app_dashboard/templates/overview.html`: renders detail and storefront links.
- `tests/test_web.py`: covers both rendered behaviors and app-scope preservation.

### Task 1: Pin The Merchant UI Behavior

**Files:**
- Modify: `tests/test_web.py`

- [x] **Step 1: Add a failing free-plan rendering test**

Insert an installed shop, active zero-value subscription, and matching monthly
charge. Request its detail page and assert `Free plan` and
`No recurring charge` appear while `$0` does not.

- [x] **Step 2: Add a failing recent-activity link test**

Insert a shop and lifecycle event, request `/?app=test-app`, and assert the shop
name links to `/customers/<encoded-gid>?app=test-app` while a separate
`https://<domain>` storefront link is present.

- [x] **Step 3: Run the focused tests and verify they fail**

Run:

```bash
uv run pytest tests/test_web.py -k 'free_plan or latest_activity_links' -q
```

Expected: failures because zero is rendered as `$0 /mo` and recent activity
does not yet expose link targets.

### Task 2: Implement The Minimal Presentation And Query Changes

**Files:**
- Modify: `src/app_dashboard/stats.py`
- Modify: `src/app_dashboard/templates/customer.html`
- Modify: `src/app_dashboard/templates/overview.html`

- [x] **Step 1: Return recent-event shop identity**

Select `e.shop_gid` and `s.shop_domain` in `recent_events`, then expose them as
`shop_gid` and `shop_domain` in each returned dictionary.

- [x] **Step 2: Render free subscriptions explicitly**

Before the annual/monthly amount branches, render `Free plan` and
`No recurring charge` when an active subscription has `monthly_amount == 0`.

- [x] **Step 3: Render both activity links**

Make the shop name an internal detail link that preserves the selected app.
When a domain exists, add a separate HTTPS `storefront` link.

- [x] **Step 4: Run the focused tests**

Run:

```bash
uv run pytest tests/test_web.py -k 'free_plan or latest_activity_links' -q
```

Expected: both tests pass.

### Task 3: Verify The Complete Dashboard

**Files:**
- No code changes expected.

- [x] **Step 1: Run the full test suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [x] **Step 2: Smoke-test live Bol Sync data**

Open COTE CHIC. and Bol Sync Overview locally. Verify the detail card says
`Free plan`, the activity shop name opens merchant detail, and `storefront`
targets the merchant domain.

- [x] **Step 3: Commit the implementation**

```bash
git add src/app_dashboard/stats.py src/app_dashboard/templates/customer.html \
  src/app_dashboard/templates/overview.html tests/test_web.py
git commit -m "Clarify merchant plans and link activity shops"
```
