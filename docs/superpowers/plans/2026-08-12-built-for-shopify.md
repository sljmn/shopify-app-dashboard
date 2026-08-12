# Built for Shopify Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect Shopify's official Built for Shopify badge and expose a truthful tri-state status in Discover and public app details.

**Architecture:** Category cards provide broad BFS coverage; full listing snapshots provide authoritative status and immutable change history. `discovered_apps` stores the latest checked state and timestamp, while `discovery_listing_snapshots.listing` retains BFS in historical versions.

**Tech Stack:** Python 3.13, BeautifulSoup, PostgreSQL, FastAPI, Jinja2, pytest.

---

### Task 1: Persist and parse BFS status

**Files:**
- Create: `src/app_dashboard/migrations/022_built_for_shopify.sql`
- Modify: `src/app_dashboard/listing_intelligence.py`
- Modify: `src/app_dashboard/app_store_discovery.py`
- Test: `tests/test_listing_intelligence.py`
- Test: `tests/test_app_store_discovery.py`

- [ ] **Step 1: Add failing parser tests**

Assert that listing HTML containing `.built-for-shopify-badge` returns
`built_for_shopify=True`, HTML without it returns `False`, and category cards
populate `CategoryApp.built_for_shopify` from the same official marker.

- [ ] **Step 2: Run parser tests and verify failure**

Run: `.venv/bin/pytest -q tests/test_listing_intelligence.py tests/test_app_store_discovery.py -x`

Expected: failure because neither parser exposes `built_for_shopify`.

- [ ] **Step 3: Add the current-state columns**

Create migration 022:

```sql
alter table discovered_apps
  add column if not exists built_for_shopify boolean,
  add column if not exists bfs_checked_at timestamptz;

create index if not exists discovered_apps_bfs_idx
  on discovered_apps (built_for_shopify, bfs_checked_at desc);
```

- [ ] **Step 4: Implement stable badge parsing**

Add `built_for_shopify` to `LISTING_FIELDS`, return
`soup.select_one('.built-for-shopify-badge') is not None` from `parse_listing`,
and extend `CategoryApp` with `built_for_shopify: bool = False`. Set the field
from each category card's `.built-for-shopify-badge`.

- [ ] **Step 5: Run parser tests**

Run: `.venv/bin/pytest -q tests/test_listing_intelligence.py tests/test_app_store_discovery.py -x`

Expected: all pass.

### Task 2: Update current status from successful scans

**Files:**
- Modify: `src/app_dashboard/app_store_discovery.py`
- Modify: `src/app_dashboard/discovery_watchlist.py`
- Test: `tests/test_app_store_discovery.py`
- Test: `tests/test_discovery_watchlist.py`

- [ ] **Step 1: Add failing persistence tests**

Verify `sync_discovery_categories` sets both `built_for_shopify` and
`bfs_checked_at` for every returned category card. Verify
`store_competitor_snapshot` sets the current columns from the parsed listing and
that changing BFS creates a `built_for_shopify` listing diff.

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest -q tests/test_app_store_discovery.py tests/test_discovery_watchlist.py -x`

Expected: current-state columns remain null.

- [ ] **Step 3: Persist category and listing observations**

Extend the category upsert with:

```sql
built_for_shopify=excluded.built_for_shopify,
bfs_checked_at=excluded.bfs_checked_at
```

Pass the successful scan timestamp for `bfs_checked_at`. In
`store_competitor_snapshot`, update the same columns from
`listing['built_for_shopify']` on both reused and new snapshot paths. Do not
touch either value from failure handlers.

- [ ] **Step 4: Run persistence tests**

Run: `.venv/bin/pytest -q tests/test_app_store_discovery.py tests/test_discovery_watchlist.py -x`

Expected: all pass.

### Task 3: Add BFS filtering and reporting

**Files:**
- Modify: `src/app_dashboard/app_store_discovery.py`
- Modify: `src/app_dashboard/discovery_watchlist.py`
- Modify: `src/app_dashboard/web.py`
- Test: `tests/test_app_store_discovery.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: Add failing report tests for all three states**

Insert one `true`, one `false`, and one null app. Assert catalog/report rows
contain `built_for_shopify` and `bfs_checked_at`, and filters `bfs`, `not_bfs`,
and `unknown` each return only the matching app.

- [ ] **Step 2: Run report tests and verify failure**

Run: `.venv/bin/pytest -q tests/test_app_store_discovery.py tests/test_web.py -x`

Expected: report signatures reject or ignore the BFS filter.

- [ ] **Step 3: Extend report queries and route parameters**

Add `bfs: str = ""` to `discovery_report` and `search_app_catalog`. Normalize it
to `{"bfs", "not_bfs", "unknown"}` and append respectively:

```sql
app.built_for_shopify is true
app.built_for_shopify is false
app.built_for_shopify is null
```

Select the status and timestamp in report rows and `app_detail`. Add route query
parameters `bfs` and `catalog_bfs`, preserve them in pager query strings, and
pass them into templates.

- [ ] **Step 4: Run report tests**

Run: `.venv/bin/pytest -q tests/test_app_store_discovery.py tests/test_web.py -x`

Expected: all pass.

### Task 4: Present BFS in Discover and app details

**Files:**
- Modify: `src/app_dashboard/templates/discover.html`
- Modify: `src/app_dashboard/templates/discovered_app.html`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] **Step 1: Add failing rendered-page assertions**

Assert a BFS catalog row shows `Built for Shopify`, a checked false row shows
`Not BFS`, an unchecked row shows `Not checked`, the filter selects correctly,
and a BFS detail heading renders its badge.

- [ ] **Step 2: Run web tests and verify failure**

Run: `.venv/bin/pytest -q tests/test_web.py -x`

Expected: BFS labels are absent.

- [ ] **Step 3: Add compact badges, columns, and filters**

Use a small green status badge for BFS, neutral text for `Not BFS`, and muted
text for `Not checked`. Add the select to both Discover filter forms and a BFS
column to activity/catalog tables. Add a detail-heading badge and an overview
card showing status plus `bfs_checked_at`. Keep mobile tables within the
existing responsive catalog pattern.

- [ ] **Step 4: Run focused and complete verification**

Run:

```bash
.venv/bin/pytest -q tests/test_web.py tests/test_listing_intelligence.py \
  tests/test_app_store_discovery.py tests/test_discovery_watchlist.py -x
.venv/bin/pytest -q
uvx ruff check src/app_dashboard/listing_intelligence.py \
  src/app_dashboard/app_store_discovery.py src/app_dashboard/discovery_watchlist.py
git diff --check
```

Expected: all tests and checks pass.

- [ ] **Step 5: Verify desktop and mobile in a real browser**

Open `/discover?catalog_bfs=bfs` and a BFS app detail at 1440×900 and 390×844.
Confirm no horizontal overflow, badges do not resize rows, and unknown status is
visually distinct from a confirmed negative.

- [ ] **Step 6: Commit, push, deploy, and refresh data**

Commit the implementation on `master`, push `fork master`, deploy with
`GIT_SSH_COMMAND="ssh -o IdentityAgent=$SSH_AUTH_SOCK" git push dokku-mantle master`,
then run the existing category and watchlist listing jobs once so production BFS
coverage starts immediately.
