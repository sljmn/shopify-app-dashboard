# Store-wide Review Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture public reviews for every active indexed Shopify app and expose an explainable, category-relative review-growth dashboard.

**Architecture:** Extend the existing idempotent review collector with a database-backed rotating queue rather than adding a second scraper. Add a focused `review_intelligence.py` query/scoring module so collection, analytics, and presentation remain separate. Render a dedicated authenticated Discover reviews page using the established server-rendered FastAPI/Jinja patterns.

**Tech Stack:** Python, FastAPI, Jinja2, PostgreSQL, APScheduler, httpx, BeautifulSoup, pytest, Playwright.

---

### Task 1: Store-wide bounded collection queue

**Files:**
- Modify: `src/app_dashboard/review_collector.py`
- Modify: `src/app_dashboard/config.py`
- Modify: `src/app_dashboard/scheduler.py`
- Test: `tests/test_review_collector.py`

- [ ] Add tests proving active apps are selected fairly by oldest successful attempt, followed/recent growers receive priority, delisted apps are excluded, and batch size is bounded.
- [ ] Add settings for per-run app and page budgets with strict bounds.
- [ ] Replace the watchlist-only target query with a store-wide rotating queue while retaining priority for manually followed and recent-growing apps.
- [ ] Run `pytest tests/test_review_collector.py tests/test_scheduler.py -q` and verify all tests pass.

### Task 2: Category-relative review intelligence

**Files:**
- Create: `src/app_dashboard/review_intelligence.py`
- Test: `tests/test_review_intelligence.py`

- [ ] Seed multiple categories with incumbents, ordinary apps, and accelerating challengers; assert classification is relative to each category rather than a fixed review threshold.
- [ ] Implement percentile ranks, velocity, acceleration, top-ten concentration, active-grower share, opportunity, gem score, and confidence as bounded and explainable values.
- [ ] Add filtered review-feed and app-table queries for period, category, rating, pricing, BFS, and smart preset.
- [ ] Run `pytest tests/test_review_intelligence.py -q` and verify all tests pass.

### Task 3: Reviews dashboard

**Files:**
- Create: `src/app_dashboard/templates/discover_reviews.html`
- Modify: `src/app_dashboard/templates/_discover_nav.html`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `src/app_dashboard/web.py`
- Test: `tests/test_web.py`

- [ ] Add authenticated `/discover/reviews` route and response tests for feed rows, intelligence rows, filters, and empty states.
- [ ] Build a responsive filter bar, smart-preset tabs, metric strip, explainable intelligence table, and newest-review feed.
- [ ] Keep app/detail and Shopify source links directly actionable on desktop and mobile.
- [ ] Run focused web tests and inspect desktop/mobile screenshots with Playwright.

### Task 4: Verification and release

**Files:**
- Modify only files required by verification fixes.

- [ ] Run the focused collector, intelligence, scheduler, migration, and web tests.
- [ ] Run the complete test suite and Python compilation.
- [ ] Apply production migration requirements, commit to `master`, push the fork, deploy to Dokku, and verify `/healthz` plus authenticated route behavior.
