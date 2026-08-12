# Newest Discovered Apps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show all non-baseline App Store apps directly below the Discover week chart in newest-first order with their first-observed time.

**Architecture:** Reuse `discovery_report` and its existing ordered `rows`; only the presentation hierarchy changes. The server-side `local_time` Jinja filter remains responsible for Europe/Amsterdam conversion.

**Tech Stack:** FastAPI, Jinja2, PostgreSQL, pytest.

---

### Task 1: Lock the chronological Discover behavior

**Files:**
- Modify: `tests/test_web.py`

- [ ] Add two non-baseline apps with distinct UTC timestamps to `test_discover_is_authenticated_and_shows_new_apps_without_owned_app_scope`.
- [ ] Assert `Newest apps found` occurs before `Growth signals`, the newer app occurs before the older app, and the rendered timestamp reflects Europe/Amsterdam time.
- [ ] Run `.venv/bin/pytest -q tests/test_web.py -k discover_is_authenticated` and confirm the placement assertion fails before the template change.

### Task 2: Promote the chronological list

**Files:**
- Modify: `src/app_dashboard/templates/discover.html`

- [ ] Move the existing new-app filters, table, and pager directly below `New apps per week`.
- [ ] Add a `Newest apps found` heading and a concise explanation of `First observed`.
- [ ] Render `first_seen` with `%-d %b %Y %H:%M` through `local_time`.
- [ ] Keep Growth signals unchanged below the chronological list.
- [ ] Run `.venv/bin/pytest -q tests/test_web.py -k discover` and confirm the Discover tests pass.

### Task 3: Verify and ship

**Files:**
- Modify: none

- [ ] Run `git diff --check`.
- [ ] Run `.venv/bin/pytest -q` and confirm the full suite passes.
- [ ] Commit the implementation, push `master` to `fork`, and deploy `master` to `dokku-mantle`.
- [ ] Check `https://mantle.newcraft.dev/auth/login` returns HTTP 200 after deployment.
