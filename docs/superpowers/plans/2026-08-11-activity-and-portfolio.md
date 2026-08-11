# Activity and Portfolio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a trustworthy all-app activity feed and portfolio unit economics while removing Markdown, JSON, FAQ, and Tips features.

**Architecture:** Keep reporting queries in `stats.py`, route orchestration in `web.py`, and rendering in focused Jinja templates. Reuse `Scope`, `overview_stats`, `unit_economics`, and `current_trials` so every figure follows the same app and billing rules as the existing reports.

**Tech Stack:** Python 3.13, FastAPI, Jinja2, PostgreSQL, psycopg, pytest.

---

### Task 1: Remove obsolete exports and tips

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `src/app_dashboard/metrics.py`
- Modify: `README.md`
- Modify: `docs/configuration.md`
- Delete: `src/app_dashboard/export.py`
- Delete: `src/app_dashboard/markdown_export.py`
- Delete: `src/app_dashboard/faq.py`
- Delete: `src/app_dashboard/templates/faq.html`
- Delete: `docs/exports.md`
- Delete: `tests/test_export.py`
- Modify: `tests/test_web.py`
- Modify: `tests/test_customers.py`

- [ ] Remove export, Markdown, and FAQ imports and routes from `web.py`.
- [ ] Remove the Copy MD, Download JSON, and Tips markup and JavaScript from `base.html`.
- [ ] Delete obsolete modules, templates, docs, and their tests.
- [ ] Replace old positive route tests with 404 and absence assertions.
- [ ] Run `uv run pytest tests/test_web.py tests/test_customers.py -q` and expect all tests to pass.

### Task 2: Add the activity query and page

**Files:**
- Modify: `src/app_dashboard/stats.py`
- Modify: `src/app_dashboard/web.py`
- Create: `src/app_dashboard/templates/activity.html`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `src/app_dashboard/templates/overview.html`
- Modify: `tests/test_stats.py`
- Modify: `tests/test_web.py`

- [ ] Add failing query tests for app scope, event/date filters, reverse ordering, MRR delta, and pagination.
- [ ] Add an `activity_feed` query that returns rows, total, page, and page count from `app_events`.
- [ ] Add `/activity` with validated date, event-type, and page inputs.
- [ ] Add Activity to the sidebar and link the Overview table heading to it.
- [ ] Render merchant, app, storefront, timestamp, and signed MRR delta columns with preserved filters.
- [ ] Run `uv run pytest tests/test_stats.py tests/test_web.py -q` and expect all tests to pass.

### Task 3: Expand the portfolio table

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/overview.html`
- Modify: `tests/test_web.py`
- Modify: `tests/test_stats.py`

- [ ] Add a failing page test for MRR, installed, paying, paid share, churn, LTV, trials, and trial MRR per app.
- [ ] Compose each app row from `overview_stats`, `unit_economics`, and `current_trials` under one app scope.
- [ ] Render unknown LTV as an em dash and make every app name a scoped link.
- [ ] Keep trial conversion absent until historical trial outcomes exist.
- [ ] Run `uv run pytest tests/test_stats.py tests/test_trials.py tests/test_web.py -q` and expect all tests to pass.

### Task 4: Verify the complete product

**Files:**
- Modify: `README.md`

- [ ] Run `uv run pytest -q` and expect the complete suite to pass.
- [ ] Run the invariant checker against the configured local database and expect every invariant to pass.
- [ ] Restart or reload the local server and verify `/activity` and `/` at desktop and mobile widths.
- [ ] Confirm `/export.json`, `/index.md`, `/customers/<gid>.md`, and `/faq` return 404.
- [ ] Confirm Copy MD, Download JSON, and Tips do not appear anywhere in the interface.
- [ ] Commit the implementation with a message that explains consolidation before listing the UI changes.
