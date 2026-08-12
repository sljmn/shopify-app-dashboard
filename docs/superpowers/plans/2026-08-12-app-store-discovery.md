# App Store Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reliable store-wide Shopify app discovery, complete category enrichment, and weekly new-app reporting to Mantle.

**Architecture:** A focused `app_store_discovery.py` module owns public Shopify parsing, bounded HTTP collection, persistence, and reporting queries. Scheduler wrappers isolate network failures and database lifetimes. A new authenticated `/discover` route renders an app-independent dashboard page from the persisted index.

**Tech Stack:** Python 3.13, FastAPI, psycopg/PostgreSQL, httpx, BeautifulSoup, APScheduler, Jinja2, pytest.

---

### Task 1: Persist discovery state

**Files:**
- Create: `src/app_dashboard/migrations/016_app_store_discovery.sql`
- Modify: `tests/test_migrations.py`

- [ ] Add a failing migration test that expects `discovered_apps`, `discovery_categories`, `discovered_app_categories`, and `discovery_state`, plus unique handle/slug/membership constraints.
- [ ] Run `uv run pytest tests/test_migrations.py -q` and verify it fails because the tables do not exist.
- [ ] Create the four tables. Store `first_seen_at`, `last_seen_at`, `listing_updated_on`, and `is_baseline` on apps; use foreign keys with cascade only for category membership.
- [ ] Run the migration tests and verify they pass.

### Task 2: Parse Shopify discovery sources

**Files:**
- Create: `src/app_dashboard/app_store_discovery.py`
- Create: `tests/test_app_store_discovery.py`

- [ ] Add fixtures/tests for app sitemap entries, category sitemap slugs, category names, app-card names/handles, duplicate handles, and pagination termination.
- [ ] Run the focused tests and verify the parser tests fail.
- [ ] Implement typed parsing helpers using XML parsing for sitemaps and BeautifulSoup for category HTML. Reject non-app locale/section roots and invalid handles.
- [ ] Implement bounded `httpx` collection with the repository's retryable status behavior, request timeouts, maximum category pages, and a polite delay.
- [ ] Run the focused tests and verify parsing and request-boundary tests pass.

### Task 3: Make baseline and incremental imports truthful

**Files:**
- Modify: `src/app_dashboard/app_store_discovery.py`
- Modify: `tests/test_app_store_discovery.py`

- [ ] Add tests proving the initial import marks every row as baseline, a repeat import preserves `first_seen_at`, and a later unseen handle is non-baseline.
- [ ] Add tests proving an empty or failed source does not mark a baseline complete or mutate existing rows.
- [ ] Implement batched upserts and a single-row `discovery_state` update after a successful non-empty import.
- [ ] Implement category upserts and transactional replacement of memberships only after a complete non-empty category crawl.
- [ ] Run the focused tests and verify all persistence cases pass.

### Task 4: Report weekly discovery growth

**Files:**
- Modify: `src/app_dashboard/app_store_discovery.py`
- Modify: `tests/test_app_store_discovery.py`

- [ ] Add tests for current-week, rolling 7-day, 12-week series, search, category filtering, and one-count-per-app behavior.
- [ ] Implement reporting queries that exclude baseline rows and use Europe/Amsterdam Monday week boundaries.
- [ ] Add bounded newest-first pagination and category facets.
- [ ] Run the focused tests and verify report totals and boundaries pass.

### Task 5: Schedule collection independently

**Files:**
- Modify: `src/app_dashboard/scheduler.py`
- Modify: `tests/test_scheduler.py`

- [ ] Add tests that each wrapper closes its connection, returns a structured failure, and does not raise into unrelated jobs.
- [ ] Add a daily sitemap job and a twice-weekly category job with stable IDs and delayed first runs.
- [ ] Run scheduler tests and verify registration and isolation pass.

### Task 6: Add the Discover interface

**Files:**
- Create: `src/app_dashboard/templates/discover.html`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `src/app_dashboard/web.py`
- Modify: `tests/test_web.py`

- [ ] Add authenticated route tests for page metrics, baseline copy, query preservation, category filtering, pagination, and Shopify outbound links.
- [ ] Add an app-independent `/discover` route and include it in the sidebar without forwarding the selected owned-app scope.
- [ ] Build the 12-week series, compact metric band, filters, and responsive results table using existing tokens and components.
- [ ] Run the web tests and verify the route and rendered states pass.

### Task 7: Verify end to end

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`

- [ ] Document observation-date semantics, baseline behavior, cadence, and manual operational entry points.
- [ ] Run `uv run pytest -q` and require the complete suite to pass.
- [ ] Run a read-only smoke fetch against Shopify's app and category sitemaps and confirm non-empty parsing.
- [ ] Start the local app, seed controlled discovery rows, and inspect `/discover` at desktop and mobile sizes with Playwright.
- [ ] Confirm no overlap, clipped labels, blank charts, console errors, or selected-app leakage.

