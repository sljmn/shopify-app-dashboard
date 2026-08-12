# Discovery Events And Opportunities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reliable App Store event model and expose new apps, listing updates, delistings, enrichment, category opportunities, and alerts as distinct product concepts.

**Architecture:** Extend the existing sitemap sync with idempotent state transitions and an immutable event ledger. Reuse the existing watchlist listing snapshots for verified diffs and enrichment; reporting joins only the latest snapshot and observation.

**Tech Stack:** Python 3.13, FastAPI, PostgreSQL, Jinja2, APScheduler, httpx, pytest, Playwright.

---

### Task 1: Persist sitemap lifecycle events

**Files:**
- Create: `src/app_dashboard/migrations/019_discovery_events.sql`
- Modify: `src/app_dashboard/app_store_discovery.py`
- Test: `tests/test_app_store_discovery.py`

- [ ] Add `missing_scan_count`, `delisted_at`, `discovery_app_events`, category follows, and durable alert tables.
- [ ] Test discovered, updated, third-miss delisted, and relisted transitions.
- [ ] Update `sync_discovered_apps` to perform those transitions only after a non-empty complete scan.
- [ ] Run `.venv/bin/pytest -q tests/test_app_store_discovery.py`.

### Task 2: Report distinct event types

**Files:**
- Modify: `src/app_dashboard/app_store_discovery.py`
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/discover.html`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] Return seven-day new/update/delisted counts and event-filtered activity.
- [ ] Start weekly chart data at the first real event.
- [ ] Add separate UI labels and event filters; never call sitemap `lastmod` a creation date.
- [ ] Apply category filtering to growth signals and show the next category scan in empty states.
- [ ] Run `.venv/bin/pytest -q tests/test_web.py -k discover`.

### Task 3: Add enrichment, diffs, and category opportunities

**Files:**
- Modify: `src/app_dashboard/discovery_watchlist.py`
- Modify: `src/app_dashboard/app_store_discovery.py`
- Modify: `src/app_dashboard/templates/discover.html`
- Modify: `src/app_dashboard/templates/discovered_app.html`
- Test: `tests/test_discovery_watchlist.py`
- Test: `tests/test_app_store_discovery.py`

- [ ] Automatically follow newly discovered apps for one initial enrichment snapshot.
- [ ] Return normalized pricing/developer fields and developer app count in Discover.
- [ ] Link each changed listing version directly to its before/after comparison.
- [ ] Compute category saturation with explicit snapshot coverage.
- [ ] Run the focused discovery and watchlist tests.

### Task 4: Add category follows and Slack alerts

**Files:**
- Modify: `src/app_dashboard/discovery_watchlist.py`
- Modify: `src/app_dashboard/scheduler.py`
- Modify: `src/app_dashboard/templates/watchlist.html`
- Modify: `src/app_dashboard/web.py`
- Test: `tests/test_discovery_watchlist.py`
- Test: `tests/test_scheduler.py`
- Test: `tests/test_web.py`

- [ ] Add authenticated category follow/unfollow routes and controls.
- [ ] Create durable alerts for new category members and verified listing changes.
- [ ] Deliver undelivered alerts through the existing Slack webhook with successful-delivery timestamps.
- [ ] Keep alerts visible and retryable when Slack is not configured.

### Task 5: Verify and deploy

**Files:**
- Modify: none

- [ ] Run `git diff --check` and `.venv/bin/pytest -q`.
- [ ] Verify Discover and Watchlist at desktop and 390px mobile widths with Playwright.
- [ ] Commit, push `master` to `fork`, deploy to `dokku-mantle`, and verify the authenticated live pages plus login healthcheck.
