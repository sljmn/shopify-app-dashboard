# Mobile Navigation And Tracking State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show truthful GA4 readiness and make the mobile header and app picker compact, searchable, and keyboard-safe.

**Architecture:** Derive the operational GA4 display state in the existing integrations query without changing persisted configuration. Refine the existing progressive-enhancement app picker and sidebar through scoped responsive CSS while retaining native dialog and select fallbacks.

**Tech Stack:** Python 3.13, psycopg, Jinja2, HTML dialog, vanilla JavaScript, CSS, pytest, Playwright.

---

### Task 1: Derive truthful tracking readiness

**Files:**
- Modify: `src/app_dashboard/integrations.py`
- Modify: `src/app_dashboard/templates/integrations.html`
- Modify: `src/app_dashboard/templates/runbook.html`
- Test: `tests/test_integrations.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write failing read-model tests**

Add tests that save a configured app with `tracking_status='connected'`, assert `tracking_display_status == 'awaiting_data'` before any `ga4_daily` row exists, insert one app-owned GA4 row, and assert the state becomes `connected`. Add a rendered-page assertion for `Awaiting first data`.

- [ ] **Step 2: Verify the tests fail**

Run `uv run --frozen pytest -q tests/test_integrations.py tests/test_web.py -k 'tracking or awaiting'` and expect failures because `tracking_display_status` does not exist.

- [ ] **Step 3: Implement the derived state**

Extend `integration_rows` with an `exists` query against `ga4_daily` scoped by `app_id`. Return `tracking_display_status='awaiting_data'` only when configured status is connected and no row exists. Render that value and document the distinction in the runbook.

- [ ] **Step 4: Verify focused tests pass**

Run `uv run --frozen pytest -q tests/test_integrations.py tests/test_web.py -k 'tracking or awaiting'` and expect all selected tests to pass.

### Task 2: Compact the mobile header and app picker

**Files:**
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] **Step 1: Add failing structural assertions**

Assert the response includes stable hooks for the wordmark secondary label, account identity, and scrollable app-picker body. These hooks make the intended mobile behavior testable without coupling tests to generated styles.

- [ ] **Step 2: Verify the structural test fails**

Run `uv run --frozen pytest -q tests/test_web.py -k 'app_picker or mobile_header'` and expect the new hook assertion to fail.

- [ ] **Step 3: Implement responsive layout**

Add the hooks and update the `max-width: 620px` rules so the secondary wordmark and account name are hidden, controls stay on one row, and the app selector is a compact second row. Change the mobile dialog to a near-full-height grid with safe-area-aware top spacing, fixed header/search rows, and a scrollable options row. Keep desktop positioning unchanged.

- [ ] **Step 4: Verify focused tests pass**

Run `uv run --frozen pytest -q tests/test_web.py -k 'app_picker or mobile_header'` and expect all selected tests to pass.

### Task 3: Browser and regression verification

**Files:**
- Modify: `docs/plans/2026-08-12-mobile-navigation-and-tracking-state-design.md` only if verification reveals a documented constraint that changed.

- [ ] **Step 1: Run the complete suite**

Run `uv run --frozen pytest -q` and expect all tests to pass.

- [ ] **Step 2: Start the local server**

Run the project server on an unused local port with the existing development database and authentication configuration.

- [ ] **Step 3: Verify mobile and desktop visually**

Use Playwright at 390x844 and 1440x900. Capture the normal header and open picker, type a partial app name, and confirm the matching option remains visible above the mobile viewport boundary. Check `document.documentElement.scrollWidth === document.documentElement.clientWidth`.

- [ ] **Step 4: Commit, push, and deploy**

Commit the tested implementation with a message explaining that operational status now requires data and mobile navigation uses the available viewport. Push `master` to the fork and Dokku remotes, then verify `/healthz` and the live mobile layout.
