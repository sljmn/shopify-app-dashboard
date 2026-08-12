# App Growth Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rank emerging and established Shopify apps using review, rating, and category-position history collected by Discover.

**Architecture:** Enrich existing category-card parsing, persist app/category observations only after a completed crawl, and build read-side reports from dated snapshots. Render three explainable lists on the existing app-independent Discover page.

**Tech Stack:** Python 3.13, PostgreSQL, BeautifulSoup, FastAPI/Jinja2, pytest.

---

### Task 1: Add observation storage

**Files:**
- Create: `src/app_dashboard/migrations/017_discovery_growth_signals.sql`
- Modify: `tests/test_migrations.py`

- [ ] Add app-level and category-level dated observation tables with natural-key uniqueness and non-negative checks.
- [ ] Test table existence and uniqueness constraints.

### Task 2: Capture card metrics

**Files:**
- Modify: `src/app_dashboard/app_store_discovery.py`
- Modify: `tests/test_app_store_discovery.py`

- [ ] Parse review count and rating from current Shopify card markup.
- [ ] Assign absolute category position across paginated results.
- [ ] Persist one app observation and every category position after a successful crawl.
- [ ] Verify duplicates and partial failures cannot create inconsistent snapshots.

### Task 3: Build explainable growth reports

**Files:**
- Modify: `src/app_dashboard/app_store_discovery.py`
- Modify: `tests/test_app_store_discovery.py`

- [ ] Resolve latest, 7-day, and 30-day comparison observations without treating missing history as zero.
- [ ] Compute fastest growers, rising gems, and new contenders with the documented eligibility rules.
- [ ] Test baseline exclusion, category deduplication, rank direction, and ordering.

### Task 4: Add growth lists to Discover

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/discover.html`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `tests/test_web.py`

- [ ] Render three compact tabs with component metrics and honest empty/baseline states.
- [ ] Preserve search/category filters and responsive table overflow.
- [ ] Test authenticated rendering and selected-list behavior.

### Task 5: Verify and deploy

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`

- [ ] Run the focused and complete test suites.
- [ ] Verify the current Shopify card parser against a live category page.
- [ ] Inspect desktop and mobile layouts with Playwright.
- [ ] Push master, deploy via Dokku, and run the first live metrics baseline.

