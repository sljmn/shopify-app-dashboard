# ASO Rank Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a locale-aware Shopify App Store keyword tracker that stores and compares the daily organic top 100.

**Architecture:** A focused collector parses Shopify's `search_page` Turbo frame and writes atomic scan snapshots against the shared discovered-app catalog. A separate report module owns CRUD and movement queries; FastAPI/Jinja expose list, keyword, and app views.

**Tech Stack:** PostgreSQL, httpx, BeautifulSoup, APScheduler, FastAPI, Jinja, vanilla JavaScript, pytest.

---

### Task 1: Create rank-tracker storage

**Files:**
- Create: `src/app_dashboard/migrations/024_aso_rank_tracker.sql`
- Modify: `tests/test_migrations.py`

- [ ] Add failing assertions for `aso_rank_lists`, `aso_rank_keywords`, `aso_rank_scans`, and `aso_rank_results`.
- [ ] Create tables with status constraints, cascading foreign keys, uniqueness for list/term/locale/country, and indexes for latest scans and app history.
- [ ] Run `.venv/bin/pytest -q tests/test_migrations.py` and commit with `Add ASO rank tracker storage`.

### Task 2: Parse Shopify search results

**Files:**
- Create: `src/app_dashboard/rank_collector.py`
- Create: `tests/fixtures/shopify_search_frame.html`
- Create: `tests/test_rank_collector.py`

- [ ] Capture a sanitized Turbo-frame fixture containing organic app links, a story link, reviews, ratings, BFS badges, and pagination metadata.
- [ ] Write failing tests for handle/name extraction, numeric metadata, non-app exclusion, order preservation, duplicate removal, retries, five-page collection, and the 100-result cap.
- [ ] Implement `parse_search_page(html)` and `collect_keyword_results(keyword, locale, country, http_get, sleep)` using `Turbo-Frame: search_page`, query parameters, and the existing retry rules.
- [ ] Run `.venv/bin/pytest -q tests/test_rank_collector.py` and commit with `Collect Shopify keyword rankings`.

### Task 3: Persist atomic scans

**Files:**
- Modify: `src/app_dashboard/rank_collector.py`
- Test: `tests/test_rank_collector.py`

- [ ] Add failing tests proving app upserts, position ordering, idempotent same-day rescans, and rollback on an empty first page.
- [ ] Implement `sync_keyword_rankings(conn, keyword_id, ...)` to upsert `discovered_apps`, replace the keyword's same-day successful snapshot, and write exactly the collected positions in one transaction.
- [ ] Store requested locale/country and explicit status/error metadata without claiming proxy-backed geolocation.
- [ ] Run the collector tests and commit with `Persist atomic keyword rank snapshots`.

### Task 4: Add lists, keywords, and movement reports

**Files:**
- Create: `src/app_dashboard/rank_tracker.py`
- Create: `tests/test_rank_tracker.py`

- [ ] Write failing tests for list/keyword CRUD, validation, current top 100, new/dropped/returned states, and movement versus 1, 7, and 30 days.
- [ ] Implement normalized keyword and locale validation plus idempotent create/archive operations.
- [ ] Implement SQL reports using the latest successful scan on or before each comparison date; define movement as prior position minus current position.
- [ ] Add per-app keyword history queries against `aso_rank_results`.
- [ ] Run `.venv/bin/pytest -q tests/test_rank_tracker.py` and commit with `Report keyword and app rank movement`.

### Task 5: Build Rank Tracker views

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/base.html`
- Create: `src/app_dashboard/templates/rank_tracker.html`
- Create: `src/app_dashboard/templates/rank_list.html`
- Create: `src/app_dashboard/templates/rank_keyword.html`
- Create: `src/app_dashboard/templates/rank_app.html`
- Test: `tests/test_web.py`

- [ ] Add failing authenticated route tests for list CRUD, keyword CRUD, manual scan, keyword detail, app detail, and validation errors.
- [ ] Add **Rank Tracker** under ASO in the sidebar and implement GET/POST routes with redirects after mutations.
- [ ] Build compact list and keyword controls, current top 100 tables, comparison badges, scan status, direct listing links, and honest country-localization guidance.
- [ ] Add responsive CSS: stacked controls below 760px and bounded horizontal table scrolling.
- [ ] Run `.venv/bin/pytest -q tests/test_web.py` and commit with `Add ASO rank tracker interface`.

### Task 6: Schedule daily scans

**Files:**
- Modify: `src/app_dashboard/scheduler.py`
- Test: `tests/test_scheduler.py`

- [ ] Add failing tests that active keywords are selected once, failures are isolated, and connections close.
- [ ] Implement `run_rank_tracker_job` and schedule it daily after discovery/listing jobs with a stable Europe/Amsterdam cron time.
- [ ] Keep manual scans synchronous for one keyword only; the daily job processes all active keywords sequentially with polite delays.
- [ ] Run scheduler tests and commit with `Schedule daily Shopify rank tracking`.

### Task 7: Full verification and deployment

- [ ] Run `.venv/bin/pytest -q` and `git diff --check`.
- [ ] Start the local server and use Playwright at desktop and mobile widths to create a list, add a localized keyword, scan it, inspect top 100 movement states, and open an app history.
- [ ] Commit any focused visual corrections.
- [ ] Push `master` to `fork` and `dokku-mantle`; verify health checks and execute one production keyword scan.
