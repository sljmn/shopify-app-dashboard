# Discovery Research Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add durable research lists, searchable targeted notes, developer catalogs, and private Backblaze-backed attachments to Discover.

**Architecture:** PostgreSQL remains authoritative for lists, notes, relationships, and attachment metadata. A focused research module owns SQL queries, a developer-catalog module enriches existing listing snapshots, and an object-storage adapter validates and content-addresses uploads before using Backblaze's S3-compatible API. FastAPI routes and Jinja templates expose a table-first Research workspace and small entry points from Discover.

**Tech Stack:** Python 3.13, FastAPI, psycopg 3, Jinja2, BeautifulSoup, boto3, PostgreSQL, Backblaze B2, pytest.

---

### Task 1: Add the research schema

**Files:**
- Create: `src/app_dashboard/migrations/021_research_workspace.sql`
- Modify: `tests/test_migrations.py`

- [ ] **Step 1: Write a migration test for tables, foreign keys, and the exactly-one-target constraint**

Add a test that applies migrations, queries `information_schema.tables` for all seven research tables, inserts one list/app/developer, accepts one list note, and asserts PostgreSQL rejects both a targetless note and a note with two targets.

- [ ] **Step 2: Run the migration test and verify it fails**

Run: `uv run pytest tests/test_migrations.py -q`

Expected: failure because `research_lists` does not exist.

- [ ] **Step 3: Create the idempotent SQL migration**

Define `research_lists`, `research_list_apps`, `discovered_developers`,
`discovered_app_developers`, `research_notes`, `research_attachment_objects`, and
`research_note_attachments`. Use this target constraint:

```sql
check (num_nonnulls(research_list_id, discovered_app_id,
                    discovered_developer_id) = 1)
```

Add unique indexes for list/app membership, developer URL, app/developer pairs,
attachment object keys, and note/object position. Add search/order indexes on
updated timestamps and foreign keys.

- [ ] **Step 4: Run migration tests**

Run: `uv run pytest tests/test_migrations.py -q`

Expected: all migration tests pass, including a second idempotent run.

- [ ] **Step 5: Commit**

```bash
git add src/app_dashboard/migrations/021_research_workspace.sql tests/test_migrations.py
git commit -m "Create a constrained schema for discovery research"
```

### Task 2: Implement list, note, and index queries

**Files:**
- Create: `src/app_dashboard/research.py`
- Create: `tests/test_research.py`

- [ ] **Step 1: Write failing tests for list CRUD and multi-list membership**

Cover `create_list`, `update_list`, `get_list`, `list_lists`, `add_app_to_list`,
and `remove_app_from_list`. Assert one app can join two lists and repeat addition
does not duplicate membership.

- [ ] **Step 2: Write failing tests for targeted notes and the Research index**

Cover `create_note`, `get_note`, `delete_note`, `research_index`, and
`target_research`. Assert query matches title/body/app/developer/filename, type and
list filters work, and newest `updated_at` appears first.

- [ ] **Step 3: Run tests and verify they fail**

Run: `uv run pytest tests/test_research.py -q`

Expected: import failure for `app_dashboard.research`.

- [ ] **Step 4: Implement immutable DTOs and parameterized SQL**

Use dataclasses `ResearchList`, `ResearchListRow`, `ResearchNote`,
`ResearchIndexRow`, and `TargetResearch`. Validate status against
`{"active", "archived"}` and target kind against
`{"list", "app", "developer"}`. Keep all free-text values as SQL parameters;
never interpolate a filter into SQL.

- [ ] **Step 5: Ensure adding a list membership starts tracking**

After the membership insert, call:

```python
status = watch_status(conn, handle)
if status is None or not status.active:
    follow_app(conn, handle, source="manual", now=added_at)
```

Removing a membership must only delete `research_list_apps`.

- [ ] **Step 6: Run the focused tests**

Run: `uv run pytest tests/test_research.py tests/test_discovery_watchlist.py -q`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/app_dashboard/research.py tests/test_research.py
git commit -m "Add searchable lists and targeted research notes"
```

### Task 3: Normalize developers and discover their complete catalog

**Files:**
- Create: `src/app_dashboard/developer_catalog.py`
- Create: `tests/fixtures/shopify_developer.html`
- Create: `tests/test_developer_catalog.py`
- Modify: `src/app_dashboard/discovery_watchlist.py`
- Modify: `tests/test_discovery_watchlist.py`

- [ ] **Step 1: Write parser tests**

Test `normalize_developer_url` strips query/fragment, rejects non-HTTPS and
non-Shopify URLs, and preserves the `/partners/<handle>` path. Test
`parse_developer_page` extracts unique `/apps/<handle>` links and app titles from
the fixture.

- [ ] **Step 2: Write persistence tests**

Test `upsert_developer_from_listing`, `sync_developer_catalog`,
`developer_detail`, and `developers_due_for_refresh`. Assert repeat scans are
idempotent and never erase previously known apps when a fetch fails.

- [ ] **Step 3: Run tests and verify failure**

Run: `uv run pytest tests/test_developer_catalog.py -q`

Expected: import failure for the new module.

- [ ] **Step 4: Implement parsing and persistence**

Provide `normalize_developer_url(value: str) -> str | None`,
`parse_developer_page(html: str) -> tuple[DeveloperApp, ...]`,
`upsert_developer_from_listing(conn, discovered_app_id: int, listing: dict) -> int | None`,
`sync_developer_catalog(conn, developer_id: int, http_get=httpx.get, now=None) -> dict`,
and `developer_detail(conn, developer_id: int) -> dict | None`.

Use PostgreSQL `INSERT ... ON CONFLICT` statements for developers, discovered
apps, and relationships.
Store `last_scan_error` and `last_scan_attempt_at`; only set `last_scanned_at` on
success.

- [ ] **Step 5: Persist the developer whenever a new competitor snapshot is stored**

Call `upsert_developer_from_listing` inside `store_competitor_snapshot` after the
snapshot transaction succeeds. Also call it for an existing matching content
hash so older apps can be backfilled without a listing change.

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest tests/test_developer_catalog.py tests/test_discovery_watchlist.py tests/test_listing_intelligence.py -q`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/app_dashboard/developer_catalog.py tests/fixtures/shopify_developer.html tests/test_developer_catalog.py src/app_dashboard/discovery_watchlist.py tests/test_discovery_watchlist.py
git commit -m "Connect discovered apps through their Shopify developer"
```

### Task 4: Add private, content-addressed B2 storage

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/app_dashboard/config.py`
- Create: `src/app_dashboard/object_storage.py`
- Create: `tests/test_object_storage.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Add boto3 and lock dependencies**

Run: `uv add boto3`

Expected: `pyproject.toml` and `uv.lock` include boto3/botocore.

- [ ] **Step 2: Write configuration tests**

Assert all five B2 fields are optional together for test/local startup but a
partial configuration raises a Pydantic validation error. Add
`research_upload_max_bytes = 15 * 1024 * 1024` with a positive upper bound.

- [ ] **Step 3: Write storage tests with a fake S3 client**

Cover content hashing, `research/ab/<digest>` object keys, duplicate `head_object`
reuse, `put_object`, 60-second presigned GET, delete, 15 MB rejection, extension
rejection, MIME mismatch, and JPEG/PDF/OOXML/CSV/TXT signature acceptance.

- [ ] **Step 4: Implement the storage adapter**

Expose a `ResearchObjectStore` with
`validate_and_upload(upload, *, filename: str, content_type: str) -> StoredObject`,
`presigned_get(object_key: str, *, filename: str) -> str`, and
`delete(object_key: str) -> None`.

Read at most `max_bytes + 1` into a `SpooledTemporaryFile`, calculate SHA-256
while reading, inspect magic bytes/ZIP members, sanitize the original filename,
and upload with server-side encryption and `ContentDisposition: attachment`.

- [ ] **Step 5: Run storage/config tests**

Run: `uv run pytest tests/test_object_storage.py tests/test_config.py -q`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/app_dashboard/config.py src/app_dashboard/object_storage.py tests/test_object_storage.py tests/test_config.py
git commit -m "Store research attachments privately in Backblaze B2"
```

### Task 5: Connect attachment metadata to notes

**Files:**
- Modify: `src/app_dashboard/research.py`
- Modify: `tests/test_research.py`

- [ ] **Step 1: Write failing tests for attachment metadata and deduplication**

Cover `attach_object`, `note_attachments`, `attachment_detail`,
`detach_attachment`, and `delete_note`. Assert identical content creates one
object row and two relations, position is stable, and the last detach reports
that the B2 object may be deleted.

- [ ] **Step 2: Implement attachment metadata operations**

Keep B2 calls outside database functions. Return a `DetachedObject` only when no
references remain so the web layer can delete the physical object after commit.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_research.py tests/test_object_storage.py -q`

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/app_dashboard/research.py tests/test_research.py
git commit -m "Relate deduplicated B2 objects to research notes"
```

### Task 6: Build the Research index, lists, notes, and developer pages

**Files:**
- Modify: `src/app_dashboard/web.py`
- Create: `src/app_dashboard/templates/research_index.html`
- Create: `src/app_dashboard/templates/research_list.html`
- Create: `src/app_dashboard/templates/research_list_form.html`
- Create: `src/app_dashboard/templates/research_note_form.html`
- Create: `src/app_dashboard/templates/developer_detail.html`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write request tests for authenticated CRUD and search**

Test GET `/research`, list create/edit/archive, membership removal, note create,
note delete, developer detail, and attachment download. Assert unauthenticated
requests redirect to login and mutating requests reject an invalid origin/CSRF
form through `_browser_form`.

- [ ] **Step 2: Implement the table-first Research routes**

Add GET `/research`, GET/POST `/research/lists/new`, GET/POST
`/research/lists/{id}`, POST `/research/lists/{id}/archive`, GET/POST
`/research/notes/new`, POST `/research/notes/{id}/delete`, GET
`/research/developers/{id}`, and GET `/research/attachments/{id}`.

Parse uploads with Starlette's form API, pass each through `ResearchObjectStore`,
and compensate by deleting newly uploaded B2 objects when metadata persistence
fails.

- [ ] **Step 3: Implement templates and navigation**

Use existing `page-heading`, `table-card`, `segment-control`, `period-toolbar`,
`ghost`, and form-control styles. Add Research to the sidebar after Discover.
Make filter rows wrap and table cards horizontally scroll on small screens.

- [ ] **Step 4: Run request tests**

Run: `uv run pytest tests/test_web.py tests/test_research.py -q`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/app_dashboard/web.py src/app_dashboard/templates/research_*.html src/app_dashboard/templates/developer_detail.html src/app_dashboard/templates/base.html tests/test_web.py
git commit -m "Add the searchable Research workspace and detail views"
```

### Task 7: Add Research actions to Discover and app details

**Files:**
- Modify: `src/app_dashboard/app_store_discovery.py`
- Modify: `src/app_dashboard/discovery_watchlist.py`
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/discover.html`
- Modify: `src/app_dashboard/templates/discovered_app.html`
- Modify: `tests/test_app_store_discovery.py`
- Modify: `tests/test_discovery_watchlist.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write tests for research summaries in Discover and app detail**

Assert catalog rows expose list membership count, app detail exposes lists,
notes, and developer, and POST `/discover/apps/{handle}/lists` adds a membership
and begins following an inactive app.

- [ ] **Step 2: Extend report queries without N+1 SQL**

Join aggregated list membership and current developer into the catalog and app
detail queries. Do not issue one query per row.

- [ ] **Step 3: Add concise actions**

Discover rows receive an Add to list control and app-detail link; app detail gets
a Research section with Add to list, New note, current lists, recent notes, and a
developer link. Keep the existing external View listing action.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_app_store_discovery.py tests/test_discovery_watchlist.py tests/test_web.py -q`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/app_dashboard/app_store_discovery.py src/app_dashboard/discovery_watchlist.py src/app_dashboard/web.py src/app_dashboard/templates/discover.html src/app_dashboard/templates/discovered_app.html tests/test_app_store_discovery.py tests/test_discovery_watchlist.py tests/test_web.py
git commit -m "Make Discover apps directly actionable in Research"
```

### Task 8: Schedule developer catalog refreshes

**Files:**
- Modify: `src/app_dashboard/scheduler.py`
- Modify: `tests/test_scheduler.py`

- [ ] **Step 1: Write scheduler tests**

Assert `run_developer_catalog_job` scans only developers referenced by active
research lists or notes, commits each developer separately, continues after one
failure, and returns scanned/failed counts.

- [ ] **Step 2: Implement and schedule the job**

Add a daily 05:45 Europe/Amsterdam job after watchlist collection. Bound worker
concurrency with the existing `watchlist_concurrency` setting and reuse the
developer service retry/timeout behavior.

- [ ] **Step 3: Run scheduler tests**

Run: `uv run pytest tests/test_scheduler.py tests/test_developer_catalog.py -q`

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/app_dashboard/scheduler.py tests/test_scheduler.py
git commit -m "Refresh researched Shopify developer catalogs daily"
```

### Task 9: Configure and verify Backblaze and the complete UI

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Create: `docs/research-workspace.md`

- [ ] **Step 1: Document configuration and operator workflow**

Document B2 variables, accepted formats, 15 MB limit, bucket privacy, list/note
workflow, developer refresh behavior, and recovery from upload failures. Never
include actual credentials.

- [ ] **Step 2: Run the full automated suite and static checks**

Run:

```bash
uv run pytest -q
uv run python -m compileall -q src
git diff --check
```

Expected: all tests pass, compilation succeeds, no whitespace errors.

- [ ] **Step 3: Create a dedicated private Backblaze bucket**

Read existing Backblaze credentials from an approved Dokku app without printing
them, create `mantle-research-prod`, create or select a bucket-scoped key, and set
the five `B2_*` variables on Dokku app `mantle`. Confirm the bucket denies public
listing and anonymous object reads.

- [ ] **Step 4: Deploy and run migrations**

Push `master` to `fork` and `dokku-mantle`. Verify deploy logs apply migration
021 and the health endpoint succeeds.

- [ ] **Step 5: Verify desktop and mobile workflows with Playwright**

At 1440×1000 and 390×844, create a list, add a Discover app, add a note with a
PNG and PDF, search it in `/research`, open the developer page, download both
attachments, archive the list, and verify no overlap or horizontal page overflow.

- [ ] **Step 6: Verify B2 lifecycle**

Confirm one digest/object for duplicate uploads, a working authenticated
download, an anonymous 401/redirect from Mantle, and object deletion after the
last note reference is removed.

- [ ] **Step 7: Commit documentation and push**

```bash
git add .env.example README.md docs/research-workspace.md
git commit -m "Document private research storage and operations"
git push fork master
git push dokku-mantle master
```
