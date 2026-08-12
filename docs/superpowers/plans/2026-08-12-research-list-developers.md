# Research List Developers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Shopify developers to research lists and keep their complete portfolios followed.

**Architecture:** Store developer membership directly instead of copying apps into a list. Resolve portfolio apps through `discovered_app_developers`, and reuse the existing developer scanner and discovery watchlist.

**Tech Stack:** PostgreSQL migrations, Python 3.13, FastAPI, Jinja, vanilla JavaScript, pytest.

---

### Task 1: Persist developer list membership

**Files:**
- Create: `src/app_dashboard/migrations/023_research_list_developers.sql`
- Modify: `tests/test_migrations.py`

- [ ] Add a migration test expecting `research_list_developers`.
- [ ] Run `.venv/bin/pytest -q tests/test_migrations.py` and verify it fails.
- [ ] Create the join table with cascading foreign keys, `added_at`, a composite primary key, and a reverse lookup index.
- [ ] Run the migration test and commit with `Add developer membership to research lists`.

### Task 2: Add repository operations

**Files:**
- Modify: `src/app_dashboard/research.py`
- Modify: `src/app_dashboard/developer_catalog.py`
- Test: `tests/test_research.py`

- [ ] Add failing tests for `search_developers`, `add_developer_to_list`, duplicate add, remove, partner counts, and automatic following of every linked app.
- [ ] Implement ranked developer search over name and `shopify_url`, including an `in_list` boolean.
- [ ] Implement add/remove operations and extend `get_list`/`list_lists` with `developers` and `developer_count`.
- [ ] After `sync_developer_catalog`, follow all catalog apps when the developer belongs to an active research list.
- [ ] Run `.venv/bin/pytest -q tests/test_research.py tests/test_developer_catalog.py` and commit with `Track developer portfolios in research lists`.

### Task 3: Add authenticated routes and list UI

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/research_list.html`
- Modify: `src/app_dashboard/templates/research_index.html`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] Add failing web tests for developer search, add/remove POST routes, counts, and partner links.
- [ ] Add `/research/developers/search`, `/research/lists/{id}/developers`, and remove routes using the same authentication and redirect conventions as apps.
- [ ] Add a Partners section with typeahead, scan state, internal dossier link, Shopify link, and remove action.
- [ ] Add the Partners total to list headers and the research index.
- [ ] Run `.venv/bin/pytest -q tests/test_web.py`, then the full suite, and commit with `Let research lists contain Shopify partners`.

### Task 4: Deploy and verify

- [ ] Push `master` to `fork` and `dokku-mantle`.
- [ ] Verify the Dokku health checks pass.
- [ ] Add HulkApps to a test research list, confirm nine portfolio apps are followed, refresh HulkApps, and confirm the list remains linked without duplicated app memberships.
