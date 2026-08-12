# Public Review Scraping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store and display complete public Shopify App Store reviews for followed and newly discovered apps through a bounded incremental collector.

**Architecture:** A focused review collector parses newest-first Shopify review pages and upserts immutable Shopify review IDs plus mutable replies. Per-app sync state separates the daily incremental frontier from resumable historical backfill; the existing watchlist scheduler invokes it without changing category-derived review metrics.

**Tech Stack:** Python 3.13, BeautifulSoup, httpx, PostgreSQL, FastAPI, Jinja2, APScheduler, pytest, Playwright.

---

### Task 1: Persist reviews and crawl state

**Files:**
- Create: `src/app_dashboard/migrations/021_discovery_reviews.sql`
- Create: `src/app_dashboard/review_collector.py`
- Test: `tests/test_review_collector.py`

- [ ] Add `discovery_reviews` with a unique `(discovered_app_id, shopify_review_id)` key and fields for rating, date, merchant metadata, body, reply, source URL, and capture timestamps.
- [ ] Add `discovery_review_sync_state` with next backfill page, completion, attempts, successes, and error state.
- [ ] Write parser tests using representative Shopify review markup with and without a developer reply.
- [ ] Implement `parse_review_page()` using `data-merchant-review`, `data-review-content-id`, star `aria-label`, review body, merchant metadata, and reply selectors.
- [ ] Run `.venv/bin/pytest -q tests/test_review_collector.py -x` and verify parser tests pass.

### Task 2: Implement bounded incremental and historical crawling

**Files:**
- Modify: `src/app_dashboard/review_collector.py`
- Test: `tests/test_review_collector.py`

- [ ] Test that page-one incremental crawling stops after reaching known review IDs and does not duplicate rows.
- [ ] Test that historical crawling persists `next_backfill_page`, processes at most five pages per run, and marks completion on an empty/final page.
- [ ] Implement retry-backed newest-first requests and transactional upserts that update developer replies without changing the review identity.
- [ ] Record isolated failure codes and leave the previous backfill frontier unchanged after a failed request.
- [ ] Run `.venv/bin/pytest -q tests/test_review_collector.py -x`.

### Task 3: Wire review collection into watchlist enrichment

**Files:**
- Modify: `src/app_dashboard/scheduler.py`
- Modify: `src/app_dashboard/discovery_watchlist.py`
- Test: `tests/test_scheduler.py`
- Test: `tests/test_discovery_watchlist.py`

- [ ] Extend active review targets to include active watchlist apps and newly enriched apps whose review backfill is incomplete.
- [ ] Invoke review sync after each listing sync while preserving per-app failure isolation and concurrency limits.
- [ ] Ensure a `new_app` enrichment entry becomes inactive only after listing capture succeeds; incomplete review backfill remains discoverable through review sync state rather than the active listing queue.
- [ ] Run `.venv/bin/pytest -q tests/test_scheduler.py tests/test_discovery_watchlist.py -x`.

### Task 4: Add the Reviews detail view

**Files:**
- Modify: `src/app_dashboard/discovery_watchlist.py`
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/discovered_app.html`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] Add a review report query returning sync status, captured count, rating distribution, and paginated newest-first review rows.
- [ ] Add `Reviews` to the app-detail navigation and accept a validated one-to-five-star filter.
- [ ] Render review body, date, merchant country/usage duration, developer reply, backfill progress, and an explicit note that captured rows can be partial while review totals come from category scans.
- [ ] Verify table/card layout at desktop and 390px mobile widths.
- [ ] Run `.venv/bin/pytest -q tests/test_web.py -k 'discover or review' -x`.

### Task 5: Verify, deploy, and start backfill

**Files:**
- Modify: none

- [ ] Run `git diff --check` and `.venv/bin/pytest -q`.
- [ ] Run Playwright against `/discover/apps/{handle}?view=reviews` at desktop and 390px widths.
- [ ] Commit and push `master` to `fork`.
- [ ] Deploy `master` to `dokku-mantle` and verify migration `021_discovery_reviews.sql`.
- [ ] Run one bounded production review sync and report targets, stored reviews, failures, and remaining incomplete backfills.
