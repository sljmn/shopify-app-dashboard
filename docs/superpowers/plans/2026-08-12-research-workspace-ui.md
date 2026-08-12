# Research Workspace UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Research filters and list details into a polished, responsive working surface with consistent date pickers and server-side app search.

**Architecture:** Reuse the globally loaded Flatpickr enhancement and the existing research write routes. Add one bounded read-only app-search query and JSON route, then progressively enhance the ordinary add-app form with an accessible result popover.

**Tech Stack:** FastAPI, PostgreSQL, Jinja2, vanilla JavaScript, Flatpickr, pytest, Playwright CLI.

---

### Task 1: Research date controls

**Files:**
- Modify: `src/app_dashboard/templates/research_index.html`
- Test: `tests/test_web.py`

- [ ] Add a rendering assertion that both research date fields use `type="date"`, `data-datepicker`, and accessible labels.
- [ ] Run the focused test and confirm the old `data-date-picker` markup fails it.
- [ ] Replace the plain text fields with the existing `.date-control` pattern and calendar icon.
- [ ] Run the focused web test and confirm it passes.

### Task 2: Bounded app search

**Files:**
- Modify: `src/app_dashboard/research.py`
- Modify: `src/app_dashboard/web.py`
- Test: `tests/test_research.py`
- Test: `tests/test_web.py`

- [ ] Add a repository test covering name/handle matching, current-list membership, and the eight-result limit.
- [ ] Implement `search_apps(conn, query, list_id, limit=8)` with parameterized SQL and deterministic ranking.
- [ ] Add an authenticated `GET /research/apps/search` JSON route that rejects empty queries with an empty result list.
- [ ] Add route tests for empty and matching searches.
- [ ] Run the focused research and web tests.

### Task 3: List dossier and app picker

**Files:**
- Modify: `src/app_dashboard/templates/research_list.html`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] Add rendering assertions for dossier metrics, the native edit disclosure, and app-picker hooks.
- [ ] Rebuild the list header as readable metadata plus `Edit details`, `Add app`, and `New note` actions.
- [ ] Place the existing settings form inside a native disclosure without changing field names or POST action.
- [ ] Build the app search control with a visible search input, hidden canonical handle, live result list, keyboard selection, and regular form submission.
- [ ] Add scoped responsive CSS for the dossier, work sections, results, and mobile stacking.
- [ ] Run the focused web tests.

### Task 4: Verification and delivery

**Files:**
- Verify: all modified files

- [ ] Run `uv run pytest -q` and expect the complete suite to pass.
- [ ] Run `git diff --check` and `uv run python -m compileall -q src`.
- [ ] Commit the implementation on `master` and push to GitHub and Dokku.
- [ ] Verify Research index and list detail at 1440x900 and 390x844 with Playwright.
- [ ] Verify Flatpickr opens, app search selects a result, edit disclosure opens, and production `/healthz` is healthy.
