# Dashboard Error Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render signed-in errors in the normal dashboard style and redirect the retired `/activiteit` route to `/activity`.

**Architecture:** Keep the existing HTTP exception handler and shared error copy. Pass a signed-in-specific presentation flag to the existing template, let `gate.html` select the regular content treatment without cover art, and add one explicit permanent redirect route that preserves the query string.

**Tech Stack:** FastAPI, Jinja2, inline application CSS, pytest, Playwright.

---

### Task 1: Lock the browser behavior down

**Files:**
- Modify: `tests/test_web.py`

- [x] **Step 1: Add a failing signed-in error presentation test**

Assert that a signed-in unknown route returns 404, contains the dashboard sidebar
and error heading, and does not contain the `cover` body class or error artwork.

- [x] **Step 2: Add a failing legacy-route redirect test**

Request `/activiteit?on=2026-08-12&event_type=installed` without following
redirects. Assert status 308 and location
`/activity?on=2026-08-12&event_type=installed`.

- [x] **Step 3: Run the focused tests**

Run: `uv run pytest tests/test_web.py -k '404 or activiteit' -q`

Expected: the new presentation and redirect assertions fail before the
implementation.

### Task 2: Implement the dashboard-native error page

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/gate.html`
- Modify: `src/app_dashboard/templates/error.html`
- Modify: `src/app_dashboard/templates/base.html`

- [x] **Step 1: Separate signed-in error presentation from cover presentation**

Pass no artwork for signed-in errors. Add an `error-page` content class and a
small `404` eyebrow in `error.html`; retain the existing gate for signed-out
errors.

- [x] **Step 2: Add restrained error-page styles**

Use the existing surface, ink, muted, border, brand, display-font, and button
tokens. Keep the message width constrained and responsive without a decorative
card or full-screen background.

- [x] **Step 3: Redirect the old route**

Add a `GET /activiteit` endpoint returning a 308 redirect to `/activity`, with
the original query string appended unchanged.

- [x] **Step 4: Run focused and full tests**

Run: `uv run pytest tests/test_web.py -q`

Run: `uv run pytest -q`

Expected: all tests pass.

### Task 3: Verify the rendered result

**Files:**
- No source changes expected.

- [x] **Step 1: Start or reuse the local server**

Run the application on an available local port with test credentials.

- [x] **Step 2: Check desktop and mobile**

Use Playwright at 1440x900 and 390x844. Confirm the signed-in error page uses
the normal dashboard surface, text and controls do not overlap, the sidebar or
mobile navigation remains usable, and `/activiteit` resolves to `/activity`.

- [ ] **Step 3: Commit, push master, and deploy**

Commit the focused source and test changes, push `master` to `fork`, then push
`master` to `dokku-mantle`. Confirm `https://mantle.newcraft.dev/auth/login`
returns 200 after deployment.
