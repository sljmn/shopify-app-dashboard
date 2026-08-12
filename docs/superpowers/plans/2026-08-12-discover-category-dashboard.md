# Discover Category Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a selected Shopify App Store category show its complete current inventory and allow filtering that inventory by useful growth and listing signals.

**Architecture:** Add one category-focused reporting function beside the existing Discover reports, backed entirely by existing category memberships, observations, events and listing snapshots. The `/discover` route switches to category mode when `category` is present and the template renders a dedicated summary, filter toolbar and responsive inventory table while leaving global Discover unchanged.

**Tech Stack:** Python 3.13, FastAPI, psycopg/PostgreSQL, Jinja2, pytest, Playwright CLI.

---

## File map

- Modify `src/app_dashboard/app_store_discovery.py`: category inventory query, signal filters, metrics, pagination and sorting.
- Modify `src/app_dashboard/web.py`: validate category-mode filters and add the report to template context.
- Modify `src/app_dashboard/templates/discover.html`: select between global and category modes.
- Modify `src/app_dashboard/templates/base.html`: category table and responsive filter styling.
- Modify `tests/test_app_store_discovery.py`: report semantics and edge cases.
- Modify `tests/test_web.py`: route and rendered category-mode behavior.

### Task 1: Category inventory report

**Files:**
- Modify: `src/app_dashboard/app_store_discovery.py`
- Test: `tests/test_app_store_discovery.py`

- [ ] **Step 1: Write failing report tests**

Seed one category with baseline and newly discovered apps plus observations on current, 7-day and
30-day dates. Assert that the unfiltered report returns every active member ordered by latest
category position, exposes deltas, and retains rows with missing observations:

```python
report = category_dashboard(db, "anti-theft", now=NOW)
assert [row["handle"] for row in report["rows"]] == ["ranked", "unmeasured"]
assert report["rows"][0]["delta7"] == 3
assert report["rows"][0]["delta30"] == 8
assert report["rows"][1]["reviews"] is None
assert report["total_apps"] == 2
```

Add focused cases for `signal="new"`, `signal="reviews"`, `signal="fastest"`,
`signal="listing"`, `signal="delisted"`, BFS filtering, search, unknown category and pagination.

- [ ] **Step 2: Run the tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_app_store_discovery.py -x
```

Expected: import failure because `category_dashboard` does not exist.

- [ ] **Step 3: Implement the category report**

Add this public interface:

```python
def category_dashboard(
    conn, slug: str, *, search: str = "", signal: str = "all",
    bfs: str = "", page: int = 1, per_page: int = 100, now=None,
) -> dict | None:
    ...
```

Look up the category first and return `None` when it does not exist. Build inventory from
`discovered_app_categories`; lateral joins fetch the latest observation, observations on or before
7 and 30 days ago, the latest listing snapshot, latest verified listing change, and discovery or
delisting events.

Rows contain `handle`, `name`, `rank`, `reviews`, `rating`, `delta7`, `delta30`,
`built_for_shopify`, `bfs_checked_at`, `listing`, `pricing`, `developer`,
`listing_changed_at`, `first_seen_at`, `delisted_at` and `categories`. Reuse `pricing_profile`.

Supported signals are:

```python
signals = {"all", "new", "reviews", "fastest", "listing", "delisted"}
```

- `all`: active members;
- `new`: non-baseline apps with a `discovered` event;
- `reviews`: positive 30-day review movement;
- `fastest`: positive 30-day movement ordered descending;
- `listing`: verified listing change in the last 30 days;
- `delisted`: currently delisted members.

Return `total_apps`, `measured_apps`, `bfs_apps`, `new_30d`, `review_gainers_30d`, `last_scan`,
`category_name`, `rows`, `total`, `page` and `pages`.

- [ ] **Step 4: Run report tests**

```bash
.venv/bin/pytest -q tests/test_app_store_discovery.py -x
```

Expected: all tests pass.

- [ ] **Step 5: Commit the report**

```bash
git add src/app_dashboard/app_store_discovery.py tests/test_app_store_discovery.py
git commit -m "Add Discover category inventory report"
```

### Task 2: Category-mode route and interface

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/discover.html`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write failing route tests**

Seed a category with baseline apps and request:

```python
page = client.get("/discover?category=anti-theft")
```

Assert that it contains the category title, all apps, summary labels, signal selector and direct
listing links. Assert that global `App Store activity per week` and `Category opportunities`
sections are absent. Request an unknown category and assert a clear `Category not found` state.

- [ ] **Step 2: Run route tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_web.py -x
```

Expected: failure because the route still renders global event mode.

- [ ] **Step 3: Wire category mode in FastAPI**

Import `category_dashboard`, add `signal: str = "all"` and `category_page: int = 1`, validate the
signal, and call the report only when `category.strip()` is non-empty. Pass `category_report`,
`selected_signal` and a category-specific pagination query string into template context.

- [ ] **Step 4: Render the category dashboard**

Wrap the existing global content:

```jinja
{% if selected_category %}
    {# category dashboard #}
{% else %}
    {# current global Discover page #}
{% endif %}
```

Category mode includes a breadcrumb and title; cards for apps, measured apps, BFS, new apps and
review gainers; search, signal and BFS controls; and Rank, App, Reviews, 7 days, 30 days, Rating,
BFS, Pricing, Developer and Listing columns. Render unknown values as `—`. Pagination retains all
filters. Reuse the catalog table's labeled mobile-card pattern.

- [ ] **Step 5: Run web and report tests**

```bash
.venv/bin/pytest -q tests/test_web.py tests/test_app_store_discovery.py -x
```

Expected: all tests pass.

- [ ] **Step 6: Commit category mode**

```bash
git add src/app_dashboard/web.py src/app_dashboard/templates/discover.html \
  src/app_dashboard/templates/base.html tests/test_web.py
git commit -m "Show category inventory in Discover"
```

### Task 3: Full verification and release

**Files:**
- Verify all changed files

- [ ] **Step 1: Run the complete suite and diff checks**

```bash
.venv/bin/pytest -q
git diff --check
git status --short
```

Expected: the suite passes and the diff check emits no output.

- [ ] **Step 2: Verify desktop behavior with Playwright**

Start a local server against the test database, sign in and open a populated category URL. Confirm
inventory appears immediately, filters retain state, links are correct and global Discover sections
do not render.

- [ ] **Step 3: Verify mobile behavior with Playwright**

Resize to `390x844`. Confirm the toolbar stacks without overflow, rows become labeled cards, long
app names wrap and all actions remain reachable.

- [ ] **Step 4: Commit any verification fixes**

```bash
git add src tests
git commit -m "Polish Discover category dashboard"
```

Skip this commit when verification requires no changes.

- [ ] **Step 5: Push and deploy master**

```bash
git push fork master
GIT_SSH_COMMAND="ssh -o IdentityAgent=$SSH_AUTH_SOCK" git push dokku-mantle master
```

Expected: GitHub updates, Dokku healthchecks pass and production is deployed.

## Self-review

- Covers inventory, approved filters, metrics, unknown data, global-mode preservation, mobile
  behavior and direct listing links.
- Introduces no new persistence or scraper.
- Function names and filter values match across query, route, template and tests.
