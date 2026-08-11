# Manual Data Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe background control that refreshes fresh or complete data for the selected app or every app.

**Architecture:** A process-local coordinator runs one manual synchronization at a time and exposes an immutable status snapshot. It reuses the existing Partner and GA4 pipelines, adding explicit full-history switches so replay remains idempotent and does not mutate cursors before work succeeds.

**Tech Stack:** Python 3.13, FastAPI, psycopg, threading, Jinja2, vanilla JavaScript, pytest

---

## File map

- Create `src/app_dashboard/manual_sync.py`: single-job coordinator, source orchestration, and progress snapshots.
- Modify `src/app_dashboard/pipeline.py`: explicit lifecycle and transaction full-history switches.
- Modify `src/app_dashboard/ga4.py`: explicit full-history GA4 switch.
- Modify `src/app_dashboard/web.py`: coordinator lifecycle, secure start route, and status route.
- Modify `src/app_dashboard/templates/overview.html`: fetch menu and live progress region.
- Modify `src/app_dashboard/templates/base.html`: styles and status polling behavior.
- Create `tests/test_manual_sync.py`: coordinator behavior.
- Modify `tests/test_pipeline.py`, `tests/test_ga4.py`, and `tests/test_web.py`: replay and web contract tests.
- Modify `README.md` and `docs/architecture.md`: operator-visible behavior and one-process limitation.

### Task 1: Add explicit full-history pipeline boundaries

**Files:**
- Modify: `src/app_dashboard/pipeline.py`
- Modify: `src/app_dashboard/ga4.py`
- Test: `tests/test_pipeline.py`
- Test: `tests/test_ga4.py`

- [ ] **Step 1: Write failing lifecycle and transaction replay tests**

Add tests that seed stored sync state, call `run_sync(..., full_history=True)` and
`sync_transactions(..., full_history=True)`, and assert the Partner fetches
receive `after_cursor=None` and `created_at_min=None`. Retain assertions that the
default calls still use their saved incremental boundaries.

- [ ] **Step 2: Run the focused tests and confirm the new arguments fail**

Run:

```bash
uv run --frozen pytest tests/test_pipeline.py -q
```

Expected: failures reporting that `full_history` is not accepted.

- [ ] **Step 3: Implement the pipeline switches**

Extend the signatures without changing default behavior:

```python
def run_sync(conn, client, app, settings, http_post, *, full_history=False):
    cursor = None if full_history else stored_cursor

def sync_transactions(conn, client, app, settings, sleep=time.sleep, *,
                      full_history=False):
    created_at_min = None if full_history else incremental_start
```

Keep the existing terminal cursor and `last_synced_at` writes after successful
completion.

- [ ] **Step 4: Add and implement the GA4 replay test**

Test that `sync_ga4(..., force_full=True)` uses `GA4_EARLIEST_DATA` even when
rows already exist, then implement:

```python
start = earliest if force_full or not existing else today - timedelta(days=lookback_days)
```

- [ ] **Step 5: Run the focused tests**

Run:

```bash
uv run --frozen pytest tests/test_pipeline.py tests/test_ga4.py -q
```

Expected: all tests pass.

### Task 2: Build the single-job coordinator

**Files:**
- Create: `src/app_dashboard/manual_sync.py`
- Create: `tests/test_manual_sync.py`

- [ ] **Step 1: Write failing coordinator tests**

Cover these contracts with injected source callables and a synchronous thread
factory:

```python
coordinator.start([alpha], mode="fresh")
assert coordinator.status()["state"] == "complete"
assert coordinator.status()["scope"] == ["alpha"]

coordinator.start([alpha, beta], mode="all")
assert calls == [
    ("alpha", "lifecycle", True),
    ("alpha", "transactions", True),
    ("alpha", "subscriptions", True),
    ("alpha", "ga4", True),
    ("beta", "lifecycle", True),
    ("beta", "transactions", True),
    ("beta", "subscriptions", True),
    ("beta", "ga4", True),
]
```

Also assert that one failed source is recorded while later sources continue and
that a second start during `running` raises `SyncAlreadyRunning`.

- [ ] **Step 2: Run the coordinator tests and confirm the module is missing**

Run:

```bash
uv run --frozen pytest tests/test_manual_sync.py -q
```

Expected: collection fails because `app_dashboard.manual_sync` does not exist.

- [ ] **Step 3: Implement coordinator state and locking**

Create `ManualSyncCoordinator` with a `threading.Lock`, a copied status dict,
and `start(apps, mode)`. Validate `mode in {"fresh", "all"}`, reject a running
job, initialize `total_steps`, and start one daemon thread. Never expose mutable
internal status objects.

- [ ] **Step 4: Implement per-app source orchestration**

Build one Partner client per organization, then for each scoped app run:

```python
run_sync(..., full_history=full)
sync_transactions(..., full_history=full)
sync_active_subscriptions(...)
sync_ga4(..., force_full=full)  # only when GA4 is configured
```

Use a fresh database connection per source, close it in `finally`, update
`current_app`, `current_source`, and `completed_steps` under the lock, and append
sanitized errors without stopping later work.

- [ ] **Step 5: Run coordinator tests**

Run:

```bash
uv run --frozen pytest tests/test_manual_sync.py -q
```

Expected: all tests pass.

### Task 3: Add secure web endpoints

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing web tests**

Create an app with an injected coordinator and assert:

- `POST /sync` with `mode=fresh&app=test-app` starts only that app and redirects
  to `/?app=test-app`;
- no `app` starts all active apps;
- `mode=all` reaches the coordinator unchanged;
- unknown apps and modes return 404/400;
- a mismatched Origin returns 403;
- a concurrent start returns 409;
- `GET /sync/status` returns the coordinator snapshot.

- [ ] **Step 2: Run the web tests and confirm routes are absent**

Run:

```bash
uv run --frozen pytest tests/test_web.py -q
```

Expected: new route tests fail with 404.

- [ ] **Step 3: Wire one coordinator into `create_app`**

Allow an optional coordinator injection for tests and otherwise construct one
from `conn_factory` and settings. Store no token or client in response state.

- [ ] **Step 4: Implement the start boundary**

Add a capped urlencoded form parser shared only by manual sync. Require
`verify_creds`, refuse non-form bodies, compare a supplied Origin to
`PUBLIC_BASE_URL`, resolve the selected app from the catalog, and translate
coordinator validation/concurrency exceptions to explicit 4xx responses.

- [ ] **Step 5: Implement status JSON**

Return only `state`, `mode`, scope slugs, progress counts, current source/app,
timestamps, and sanitized errors from `GET /sync/status`.

- [ ] **Step 6: Run focused web tests**

Run:

```bash
uv run --frozen pytest tests/test_web.py -q
```

Expected: all tests pass.

### Task 4: Add the fetch menu and live status

**Files:**
- Modify: `src/app_dashboard/templates/overview.html`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Add failing render assertions**

Assert the Overview contains `Fetch data`, `Fetch fresh data`, and `Fetch all
data again`, that the selected app is posted as a hidden field, and that other
pages do not render the control.

- [ ] **Step 2: Render the scoped action menu**

Place a compact `<details>` menu in the health strip. Use two urlencoded POST
forms; add `confirm()` only to the full replay form. Disable both actions while
the initial coordinator snapshot is running.

- [ ] **Step 3: Add status polling**

Use nonce-protected vanilla JavaScript to poll `/sync/status` every two seconds
only while `state === "running"`. Update an `aria-live="polite"` status line,
disable the actions, and reload the current Overview URL once the state becomes
`complete` or `failed`.

- [ ] **Step 4: Add responsive styles**

Keep the health text and menu aligned on desktop; on narrow screens make the
menu full width without introducing page overflow. Use existing colors,
borders, and button dimensions.

- [ ] **Step 5: Run render tests**

Run:

```bash
uv run --frozen pytest tests/test_web.py -q
```

Expected: all tests pass.

### Task 5: Document and verify end to end

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Document manual refresh behavior**

Explain the two modes, app-selector scope, one-job limit, idempotent replay, and
the fact that a process restart cancels only the in-memory job while leaving
stored data consistent.

- [ ] **Step 2: Run formatting and complete tests**

Run:

```bash
git diff --check
uv run --frozen pytest -q
```

Expected: no whitespace errors and all tests pass.

- [ ] **Step 3: Check live database invariants**

Run:

```bash
DATABASE_URL=postgresql:///app_dashboard_multi_real uv run --frozen python scripts/check_invariants.py
```

Expected: every invariant reports `ok`.

- [ ] **Step 4: Verify in the local browser**

Restart the local server with real catalog credentials, then verify desktop and
mobile Overview layouts, selected-app scope, progress updates, duplicate-start
handling, and a completed fresh sync. Confirm there is no global horizontal
overflow and no console error.

- [ ] **Step 5: Commit the implementation**

```bash
git add README.md docs/architecture.md src tests
git commit -m "Add safe manual dashboard synchronization" \
  -m "Let operators refresh current or complete data for one app or the portfolio while preserving idempotency and visible progress."
```
