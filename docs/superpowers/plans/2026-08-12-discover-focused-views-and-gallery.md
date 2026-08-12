# Focused Discover Views And Listing Gallery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add dedicated launch and listing-update views under Discover and a full-size viewer for listing screenshots.

**Architecture:** Reuse `discovery_report` as the single event-query boundary, extending its filters and row projection instead of creating parallel discovery logic. FastAPI routes provide explicit `/discover/new` and `/discover/updates` pages rendered by focused templates, while a dependency-free dialog in `discovered_app.html` owns screenshot inspection.

**Tech Stack:** Python 3.13, PostgreSQL, FastAPI, Jinja, existing CSS and vanilla JavaScript, pytest, Playwright CLI.

---

### Task 1: Strengthen discovery event rows

**Files:**
- Modify: `src/app_dashboard/app_store_discovery.py`
- Test: `tests/test_app_store_discovery.py`

- [ ] Add failing tests proving `activity="new"` excludes baseline apps and listing-update events, and proving `activity="updated"` returns prior/current dates, verified changed fields, and before/after snapshot ids.
- [ ] Run `uv run pytest tests/test_app_store_discovery.py -q` and confirm the new update metadata assertions fail.
- [ ] Extend the existing event projection with verified snapshot metadata and pricing filtering while preserving pagination.
- [ ] Run `uv run pytest tests/test_app_store_discovery.py -q` and confirm it passes.
- [ ] Commit the query increment.

### Task 2: Add focused Discover pages

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/discover.html`
- Create: `src/app_dashboard/templates/discover_activity.html`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] Add failing authenticated route tests for `/discover/new` and `/discover/updates`, their headings, filters, correct event isolation, pagination URLs, and update diff links.
- [ ] Run the focused web tests and confirm the routes return 404.
- [ ] Extract a shared Discover context helper, add both routes, and render local subnavigation on all three pages.
- [ ] Render focused KPI copy, period/search/category/BFS/pricing filters, and responsive tables. Show changed-field badges only for verified diffs and label sitemap-only updates explicitly.
- [ ] Reduce the Overview page to summary and analysis sections, replacing its embedded activity table with links to the focused views.
- [ ] Run `uv run pytest tests/test_web.py -q` and confirm it passes.
- [ ] Commit the focused-view increment.

### Task 3: Add the listing screenshot lightbox

**Files:**
- Modify: `src/app_dashboard/templates/discovered_app.html`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] Add a failing render test asserting screenshot buttons, one dialog, accessible labels, and previous/next controls exist for current and comparison galleries.
- [ ] Run the focused test and confirm it fails.
- [ ] Wrap gallery images in buttons carrying gallery and caption metadata; add a single native dialog after the app view.
- [ ] Add scoped CSS for cursor/focus states, full-screen contain sizing, controls, counter, and 390px layout.
- [ ] Add nonce-protected JavaScript for opening, navigation, keyboard control, backdrop close, body scroll lock, and focus restoration.
- [ ] Run `uv run pytest tests/test_web.py -q` and confirm it passes.
- [ ] Commit the gallery increment.

### Task 4: Verify and deploy

**Files:**
- No production file changes expected.

- [ ] Run `uv run pytest -q`, `uv run python -m compileall -q src tests`, and `git diff --check`.
- [ ] Deploy `master` to Dokku and confirm `/healthz`, `/discover/new`, and `/discover/updates` are healthy behind authentication.
- [ ] Use Playwright at 1440x900 and 390x844 to verify both focused views, open current and archived screenshots, navigate, close via Escape, and confirm image pixels are nonblank.
- [ ] Push the verified `master` branch to the fork and report the live URLs.
