# Competitor Watchlist And Listing History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add universal App Store search, manual and automatic following, daily immutable competitor listing snapshots with archived media, growth/listing history, version comparison, and an in-dashboard weekly summary.

**Architecture:** Keep public competitor intelligence keyed to `discovered_apps.id`, separate from owned-app ASO rows keyed to `apps.id`. Reuse the existing listing parser and retry conventions, while a focused `discovery_watchlist.py` module owns follow state, snapshots, diffs, reports, and media references; `watchlist_collector.py` owns network and filesystem boundaries. Full-store discovery remains cheap, while only active followed apps receive daily listing fetches.

**Tech Stack:** Python 3.13, FastAPI/Jinja, PostgreSQL/psycopg, httpx, BeautifulSoup, local mounted media archive, APScheduler, pytest, Playwright CLI.

---

## File Map

- Create `src/app_dashboard/migrations/018_discovery_watchlist.sql`: watchlist, listing snapshots, normalized changes, media objects, and snapshot-media links.
- Create `src/app_dashboard/discovery_watchlist.py`: follow commands, read models, snapshot persistence, diffs, automatic candidate selection, and weekly report.
- Create `src/app_dashboard/watchlist_collector.py`: listing/media HTTP collection, hashing, safe archive writes, and per-app sync.
- Modify `src/app_dashboard/listing_intelligence.py`: extend the shared public listing parser with developer, subtitle, language, integration, and video fields.
- Modify `src/app_dashboard/app_store_discovery.py`: universal catalog search/detail reads and automatic candidate inputs.
- Modify `src/app_dashboard/scheduler.py`: automatic follows after a complete category crawl and a daily isolated watchlist job.
- Modify `src/app_dashboard/config.py`: validated archive path and bounded watchlist concurrency.
- Modify `src/app_dashboard/web.py`: authenticated search/detail/history/compare/media routes and same-origin follow writes.
- Modify `src/app_dashboard/templates/discover.html`: universal search and follow affordances.
- Create `src/app_dashboard/templates/discovered_app.html`: overview, growth, history, and comparison views.
- Create `src/app_dashboard/templates/watchlist.html`: current watchlist and weekly intelligence summary.
- Modify `src/app_dashboard/templates/base.html`: Discover subnavigation and responsive watchlist/detail styles.
- Modify `README.md`, `docs/architecture.md`, `docs/configuration.md`, and `docs/deploy.md`: operating contract and Dokku media mount.
- Create `tests/test_discovery_watchlist.py` and `tests/test_watchlist_collector.py`; modify discovery, scheduler, web, config, and migration tests.

### Task 1: Persist Watchlist And Versioned Listing Data

**Files:**
- Create: `src/app_dashboard/migrations/018_discovery_watchlist.sql`
- Modify: `tests/test_migrations.py`

- [ ] **Step 1: Write the failing migration assertions**

Add to `test_all_migrations_apply`:

```python
assert {
    "discovery_watchlist",
    "discovery_listing_snapshots",
    "discovery_listing_changes",
    "discovery_media_objects",
    "discovery_snapshot_media",
} <= names
```

Add uniqueness checks for one watch row per app, one content hash per app, one change per snapshot/field, one media object per digest, and one role/position per snapshot.

- [ ] **Step 2: Run the migration test and confirm it fails**

Run: `uv run --frozen pytest tests/test_migrations.py -q`

Expected: FAIL because the five tables do not exist.

- [ ] **Step 3: Create migration 018**

Use these exact ownership boundaries:

```sql
create table discovery_watchlist (
    discovered_app_id bigint primary key references discovered_apps(id) on delete cascade,
    active boolean not null default true,
    follow_source text not null check (follow_source in ('manual','rising_gem','new_contender')),
    followed_at timestamptz not null,
    unfollowed_at timestamptz,
    last_attempt_at timestamptz,
    last_success_at timestamptz,
    last_error_code text
);

create table discovery_listing_snapshots (
    id bigserial primary key,
    discovered_app_id bigint not null references discovered_apps(id) on delete cascade,
    captured_at timestamptz not null,
    content_hash text not null,
    listing jsonb not null,
    unique (discovered_app_id, content_hash)
);

create table discovery_listing_changes (
    id bigserial primary key,
    discovered_app_id bigint not null references discovered_apps(id) on delete cascade,
    snapshot_id bigint not null references discovery_listing_snapshots(id) on delete cascade,
    changed_at timestamptz not null,
    field text not null,
    before_value jsonb,
    after_value jsonb,
    unique (snapshot_id, field)
);

create table discovery_media_objects (
    digest text primary key check (digest ~ '^[0-9a-f]{64}$'),
    object_key text not null unique,
    mime_type text not null,
    byte_size bigint not null check (byte_size > 0),
    width integer,
    height integer,
    created_at timestamptz not null
);

create table discovery_snapshot_media (
    snapshot_id bigint not null references discovery_listing_snapshots(id) on delete cascade,
    digest text not null references discovery_media_objects(digest),
    role text not null check (role in ('icon','screenshot')),
    position integer not null check (position >= 0),
    source_url text not null,
    primary key (snapshot_id, role, position)
);
```

Add indexes for active watchlist scans, snapshot chronology, changes by date, and media digest lookup. Make every statement `if not exists` to match repository migration conventions.

- [ ] **Step 4: Verify migration idempotency**

Run: `uv run --frozen pytest tests/test_migrations.py -q`

Expected: PASS, including the test that runs migrations twice.

- [ ] **Step 5: Commit**

```bash
git add src/app_dashboard/migrations/018_discovery_watchlist.sql tests/test_migrations.py
git commit -m "Add competitor watchlist storage"
```

### Task 2: Implement Follow State And Automatic Candidates

**Files:**
- Create: `src/app_dashboard/discovery_watchlist.py`
- Create: `tests/test_discovery_watchlist.py`
- Modify: `src/app_dashboard/app_store_discovery.py`

- [ ] **Step 1: Write follow lifecycle tests**

Cover manual follow, idempotent repeat follow, unfollow, and re-follow preserving snapshots:

```python
follow_app(db, "alpha", source="manual", now=NOW)
follow_app(db, "alpha", source="manual", now=NOW)
assert db.execute("select count(*) from discovery_watchlist").fetchone()[0] == 1

unfollow_app(db, "alpha", now=LATER)
assert watch_status(db, "alpha").active is False

follow_app(db, "alpha", source="manual", now=LATEST)
assert watch_status(db, "alpha").active is True
assert watch_status(db, "alpha").followed_at == NOW
```

Assert an unknown handle raises `LookupError` and an invalid source raises `ValueError` before SQL.

- [ ] **Step 2: Write automatic candidate tests**

Seed a baseline grower, a qualifying young gem, and a quiet new contender using `sync_discovery_categories`. Assert:

```python
result = follow_automatic_candidates(db, now=DAY_8)
assert result == {"followed": 2, "already_followed": 0}
assert watch_status(db, "young-gem").follow_source == "rising_gem"
assert watch_status(db, "young-quiet").follow_source == "new_contender"
assert watch_status(db, "baseline-grower") is None
```

Call it twice and assert the second result follows no duplicates.

- [ ] **Step 3: Run tests and confirm missing functions**

Run: `uv run --frozen pytest tests/test_discovery_watchlist.py -q`

Expected: collection failure for the new module/functions.

- [ ] **Step 4: Implement focused follow commands**

Define this immutable read type:

```python
@dataclass(frozen=True)
class WatchStatus:
    active: bool
    follow_source: str
    followed_at: datetime
    unfollowed_at: datetime | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None

```

Implement `follow_app(conn, handle, *, source, now=None) -> WatchStatus`,
`unfollow_app(conn, handle, *, now=None) -> WatchStatus`,
`watch_status(conn, handle) -> WatchStatus | None`, and
`follow_automatic_candidates(conn, *, now=None) -> dict` in this module.

`follow_automatic_candidates` must evaluate all eligible non-baseline rows, not just the UI's top 20. Give `rising_gem` priority when an app qualifies for both signals. Never deactivate rows automatically.

- [ ] **Step 5: Run focused tests**

Run: `uv run --frozen pytest tests/test_discovery_watchlist.py tests/test_app_store_discovery.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/app_dashboard/discovery_watchlist.py src/app_dashboard/app_store_discovery.py tests/test_discovery_watchlist.py
git commit -m "Add manual and automatic app following"
```

### Task 3: Extend The Shared Public Listing Parser

**Files:**
- Modify: `src/app_dashboard/listing_intelligence.py`
- Modify: `tests/test_listing_intelligence.py`

- [ ] **Step 1: Add a realistic parser fixture test**

Build HTML with JSON-LD, app-details lists, pricing cards, developer link, language section, integration links, screenshot images, and a video. Assert the normalized result contains:

```python
{
    "name": "Alpha",
    "subtitle": "Recover more abandoned carts",
    "description": "Recover abandoned carts with automated messages.",
    "features": ["Feature one"],
    "pricing": ["Starter $9.00/month"],
    "developer": {"name": "Alpha Labs", "url": "https://apps.shopify.com/partners/alpha"},
    "languages": ["English", "Dutch"],
    "integrations": ["Klaviyo"],
    "videos": ["https://cdn.shopify.com/video.mp4"],
    "icon": "https://cdn.shopify.com/icon.png",
    "screenshots": ["https://cdn.shopify.com/screen.png"],
    "rating": "4.8",
    "rating_count": "120",
}
```

- [ ] **Step 2: Confirm the parser test fails**

Run: `uv run --frozen pytest tests/test_listing_intelligence.py -q`

Expected: FAIL on the newly asserted fields.

- [ ] **Step 3: Extend `LISTING_FIELDS` and `parse_listing`**

Add `subtitle`, `developer`, `languages`, `integrations`, and `videos`. Use stable structured selectors and JSON-LD before visible-text fallbacks. Normalize text with `_text`, strip query strings from public media URLs with `_stable_url`, deduplicate while preserving order, and return an empty list/object when a public field is absent.

- [ ] **Step 4: Verify existing owned listing behavior remains green**

Run: `uv run --frozen pytest tests/test_listing_intelligence.py tests/test_aso.py -q`

Expected: PASS. A richer parser may create one legitimate new owned-app snapshot on the next daily run; document this behavior.

- [ ] **Step 5: Commit**

```bash
git add src/app_dashboard/listing_intelligence.py tests/test_listing_intelligence.py
git commit -m "Capture richer public listing metadata"
```

### Task 4: Archive Media And Store Immutable Competitor Snapshots

**Files:**
- Create: `src/app_dashboard/watchlist_collector.py`
- Modify: `src/app_dashboard/discovery_watchlist.py`
- Create: `tests/test_watchlist_collector.py`
- Modify: `tests/test_discovery_watchlist.py`

- [ ] **Step 1: Write archive boundary tests**

Use `tmp_path` and fake HTTP responses to assert:

- identical bytes are stored once at `<root>/<first-two>/<digest>`;
- `.part` files are atomically replaced and never remain after success;
- content over 10 MiB, non-image MIME types, redirects off HTTPS, and failed responses are rejected;
- `media_path(root, digest)` rejects non-hex digests and can never escape `root`.

- [ ] **Step 2: Write snapshot transaction tests**

Assert first content creates a snapshot, identical content creates none, changed content creates exact field changes, and a media failure leaves both snapshot count and current version unchanged:

```python
first = sync_followed_listing(db, app, media_root=tmp_path, http_get=fake_get, now=NOW)
same = sync_followed_listing(db, app, media_root=tmp_path, http_get=fake_get, now=LATER)
changed = sync_followed_listing(db, app, media_root=tmp_path, http_get=changed_get, now=LATEST)
assert (first.created, same.created, changed.changed_fields) == (
    True, False, ("pricing", "screenshots"),
)
```

- [ ] **Step 3: Run tests and confirm failure**

Run: `uv run --frozen pytest tests/test_watchlist_collector.py tests/test_discovery_watchlist.py -q`

Expected: FAIL because collector and snapshot APIs do not exist.

- [ ] **Step 4: Implement safe media storage**

Define these boundaries:

```python
MAX_MEDIA_BYTES = 10 * 1024 * 1024
ALLOWED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

@dataclass(frozen=True)
class ArchivedMedia:
    digest: str
    object_key: str
    mime_type: str
    byte_size: int
    width: int | None
    height: int | None

```

Implement `archive_media(http_get, url: str, root: Path) -> ArchivedMedia` and
`media_path(root: Path, digest: str) -> Path` beside this type.

Stream with a byte limit, hash while reading, write a same-directory temporary file, `fsync`, and atomically replace. Never derive a path from a source filename.

- [ ] **Step 5: Implement all-or-nothing snapshot storage**

Define:

```python
@dataclass(frozen=True)
class CompetitorSnapshotResult:
    snapshot_id: int
    created: bool
    changed_fields: tuple[str, ...]

```

Implement `store_competitor_snapshot(conn, discovered_app_id: int, listing:
dict, media: Sequence[tuple[str, int, str, ArchivedMedia]], captured_at) ->
CompetitorSnapshotResult` beside this type.

Archive all media before opening the database transaction. In one transaction insert the snapshot, changes, media metadata, snapshot links, and successful watchlist status. On any exception, only update `last_attempt_at` and a sanitized `last_error_code`; never expose exception text.

- [ ] **Step 6: Run focused tests**

Run: `uv run --frozen pytest tests/test_watchlist_collector.py tests/test_discovery_watchlist.py tests/test_listing_intelligence.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/app_dashboard/watchlist_collector.py src/app_dashboard/discovery_watchlist.py tests/test_watchlist_collector.py tests/test_discovery_watchlist.py
git commit -m "Archive competitor listing versions and media"
```

### Task 5: Add Daily Isolated Collection And Automatic Following

**Files:**
- Modify: `src/app_dashboard/scheduler.py`
- Modify: `tests/test_scheduler.py`
- Modify: `src/app_dashboard/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write config validation tests**

Assert `WATCHLIST_MEDIA_PATH` resolves to an absolute path and `WATCHLIST_CONCURRENCY` accepts 1–4 only. Add settings:

```python
watchlist_media_path: Path = Path("/data/mantle-watchlist")
watchlist_concurrency: int = 2
```

Use Pydantic validators to reject relative paths and out-of-range concurrency.

- [ ] **Step 2: Write scheduler behavior tests**

Assert the category job calls `follow_automatic_candidates` only after a successful complete category sync. Assert `run_watchlist_job` opens/closes one connection per followed app, returns per-app success/failure summaries, and one failed listing does not stop the rest.

Assert APScheduler registers `watchlist_listings` every 24 hours, first running 60 minutes after boot.

- [ ] **Step 3: Confirm tests fail**

Run: `uv run --frozen pytest tests/test_config.py tests/test_scheduler.py -q`

Expected: FAIL on missing settings/job.

- [ ] **Step 4: Implement bounded job orchestration**

Add `run_watchlist_job(conn_factory, settings) -> list[dict]` to the scheduler.

Load active watched handles once, process with `ThreadPoolExecutor(max_workers=settings.watchlist_concurrency)`, create a fresh DB connection inside each worker, and return only handle, boolean `ok`, created flag, change count, or error code. Do not return raw URLs or exception messages.

After `run_category_discovery` succeeds, call `follow_automatic_candidates` in the same job connection and merge its counts into the logged result.

- [ ] **Step 5: Run scheduler tests**

Run: `uv run --frozen pytest tests/test_config.py tests/test_scheduler.py tests/test_app_store_discovery.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/app_dashboard/config.py src/app_dashboard/scheduler.py tests/test_config.py tests/test_scheduler.py
git commit -m "Schedule competitor listing intelligence"
```

### Task 6: Add Universal Search And Follow Writes

**Files:**
- Modify: `src/app_dashboard/app_store_discovery.py`
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/discover.html`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `tests/test_app_store_discovery.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write universal search tests**

Seed baseline and newly observed apps across categories with watch states. Assert `search_app_catalog` searches every app by handle, display name, category, and developer from the latest snapshot; returns latest reviews/rating/rank; and paginates deterministically.

- [ ] **Step 2: Write authenticated write-route tests**

Assert:

- `GET /discover?q=alpha` returns baseline matches and a Follow button;
- `POST /discover/apps/alpha/follow` activates the row and redirects to the app detail;
- `POST /discover/apps/alpha/unfollow` deactivates it;
- unauthenticated writes redirect to login;
- a hostile Origin gets 403;
- unknown handles return 404;
- GET requests never change follow state.

- [ ] **Step 3: Confirm tests fail**

Run: `uv run --frozen pytest tests/test_app_store_discovery.py tests/test_web.py -q`

Expected: FAIL because universal search and follow routes are absent.

- [ ] **Step 4: Implement `search_app_catalog`**

Return a dict containing `rows`, `total`, `page`, and `pages`. Join only the latest app observation and latest competitor snapshot. Search developer through `snapshot.listing->'developer'->>'name'`. Keep SQL parameters bound; do not interpolate search text.

- [ ] **Step 5: Add same-origin POST routes**

Reuse the existing `_browser_write` origin/session boundary in `web.py`. Add
POST routes `/discover/apps/{handle}/follow` and
`/discover/apps/{handle}/unfollow`, implemented by handlers named
`follow_discovered_app` and `unfollow_discovered_app`.

Use 303 redirects after successful writes.

- [ ] **Step 6: Update Discover UI**

Place `Search all App Store apps` above Growth signals. Search results are unframed table rows with current reviews, rank, categories, follow state, and a compact icon Follow action. On mobile switch each result to a labelled row; do not nest result cards inside the page sections.

- [ ] **Step 7: Run focused tests and Playwright smoke check**

Run:

```bash
uv run --frozen pytest tests/test_app_store_discovery.py tests/test_web.py -q
```

Then inspect `/discover?q=alpha` at 1440×900 and 390×844. Verify typing, navigation, Follow, keyboard focus, no overlap, and no console errors.

- [ ] **Step 8: Commit**

```bash
git add src/app_dashboard/app_store_discovery.py src/app_dashboard/web.py src/app_dashboard/templates/discover.html src/app_dashboard/templates/base.html tests/test_app_store_discovery.py tests/test_web.py
git commit -m "Add universal discovery search and follow controls"
```

### Task 7: Add App Detail, Growth History, And Version Comparison

**Files:**
- Modify: `src/app_dashboard/discovery_watchlist.py`
- Modify: `src/app_dashboard/web.py`
- Create: `src/app_dashboard/templates/discovered_app.html`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `tests/test_discovery_watchlist.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write read-model tests**

Test `app_detail`, `growth_history`, `listing_versions`, and `compare_versions`. Require same-category rank series, review series, exact added/removed list values, before/after scalar values, ordered media, and 404-safe ownership checks where both snapshot IDs must belong to the requested app.

- [ ] **Step 2: Write route and media-security tests**

Assert all four views render, invalid snapshot pairs return 404, `/discover/media/<digest>` requires login, unknown/invalid digests return 404, and valid archived bytes receive immutable cache headers plus their stored MIME type.

- [ ] **Step 3: Confirm tests fail**

Run: `uv run --frozen pytest tests/test_discovery_watchlist.py tests/test_web.py -q`

Expected: FAIL on missing reads/routes/template.

- [ ] **Step 4: Implement detail reads**

Use exact view allowlist `{"overview", "growth", "history", "compare"}`. Return plain dataclasses/dicts from `discovery_watchlist.py`; templates must not execute SQL or derive deltas.

Compute before/after review velocity with the nearest real observations in the seven days before and after a change. Return `None` when either side lacks observations. Label the result correlation in the read model.

- [ ] **Step 5: Implement authenticated routes**

Add GET handlers named `discovered_app_detail` for
`/discover/apps/{handle}` and `discovered_media` for
`/discover/media/{digest}`.

Use Starlette `FileResponse`; resolve paths only through `media_path`. Add `Cache-Control: private, max-age=31536000, immutable` because digests are content-addressed.

- [ ] **Step 6: Build the detail interface**

Use `Overview`, `Growth`, `Listing history`, and `Compare` tabs. Keep the current Mantle typography, borders, and purple action color. Show images as inspectable thumbnails, not decorative backgrounds. On mobile stack comparisons in chronological before/after order and constrain chart/table dimensions.

- [ ] **Step 7: Verify desktop and mobile**

Run focused tests, then Playwright against a seeded app with two versions at 1440×900 and 390×844. Verify version selection, archived image rendering, text wrapping, horizontal history containment, and zero console errors.

- [ ] **Step 8: Commit**

```bash
git add src/app_dashboard/discovery_watchlist.py src/app_dashboard/web.py src/app_dashboard/templates/discovered_app.html src/app_dashboard/templates/base.html tests/test_discovery_watchlist.py tests/test_web.py
git commit -m "Add competitor growth and listing history views"
```

### Task 8: Add Watchlist And Weekly Intelligence Summary

**Files:**
- Modify: `src/app_dashboard/discovery_watchlist.py`
- Modify: `src/app_dashboard/web.py`
- Create: `src/app_dashboard/templates/watchlist.html`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `tests/test_discovery_watchlist.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write period-safe summary tests**

Seed changes before, inside, and after a seven-day interval. Assert `watchlist_summary(conn, start, end)` contains only in-window follows, review/rank changes, and listing changes. Assert unchanged scans are absent and failures appear only as a health count, not as fake listing changes.

- [ ] **Step 2: Write watchlist page tests**

Assert `/discover/watchlist` lists active follows with source, latest review growth, last meaningful listing change, last success, and scan status. Assert `?period=7d` and `?period=30d` use the repository range allowlist rather than arbitrary SQL intervals.

- [ ] **Step 3: Confirm tests fail**

Run: `uv run --frozen pytest tests/test_discovery_watchlist.py tests/test_web.py -q`

Expected: FAIL on missing summary/page.

- [ ] **Step 4: Implement watchlist reports**

Define `list_watched_apps(conn, *, page: int = 1, per_page: int = 100) -> dict`
and `watchlist_summary(conn, start: date, end: date) -> dict`.

Summary groups must be: `new_follows`, `review_gainers`, `rank_gainers`, `listing_changes`, `patterns`, and `health`. Pattern extraction is deterministic counting of changed field/value categories; it is not LLM-generated text.

- [ ] **Step 5: Build the Watchlist page**

Add Discover-local navigation for `New apps`, `Growth signals`, and `Watchlist`. Lead with the weekly movements rather than generic metric cards. Follow with a dense sortable table. Mobile rows must retain app, growth, latest change, and health without requiring sideways page scrolling.

- [ ] **Step 6: Run tests and Playwright**

Run focused tests, then verify 7/30-day controls, empty state, failed-scan state, desktop, and mobile rendering.

- [ ] **Step 7: Commit**

```bash
git add src/app_dashboard/discovery_watchlist.py src/app_dashboard/web.py src/app_dashboard/templates/watchlist.html src/app_dashboard/templates/base.html tests/test_discovery_watchlist.py tests/test_web.py
git commit -m "Add watchlist intelligence summary"
```

### Task 9: Document, Validate, Deploy, And Establish Baselines

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/configuration.md`
- Modify: `docs/deploy.md`

- [ ] **Step 1: Document the operating contract**

Document that full-store category signals run Tuesday/Friday, watched listings run daily, unchanged content is deduplicated, media is content-addressed, and listing changes correlate with rather than prove growth impact.

Document `WATCHLIST_MEDIA_PATH` and `WATCHLIST_CONCURRENCY` without including production credentials.

- [ ] **Step 2: Document the Dokku media mount**

Add exact operator commands, replacing only the host storage path if required:

```bash
ssh root@116.203.128.186 'mkdir -p /var/lib/dokku/data/storage/mantle-watchlist && chown 10001:10001 /var/lib/dokku/data/storage/mantle-watchlist'
ssh root@116.203.128.186 'dokku storage:mount mantle /var/lib/dokku/data/storage/mantle-watchlist:/data/mantle-watchlist'
ssh root@116.203.128.186 'dokku config:set mantle WATCHLIST_MEDIA_PATH=/data/mantle-watchlist WATCHLIST_CONCURRENCY=2'
```

State that storage/config changes restart the app and must be run only during the approved deployment.

- [ ] **Step 3: Run all verification**

Run:

```bash
git diff --check
uv run --frozen pytest -q
```

Expected: all tests pass. Run Playwright at desktop/mobile for Discover search, Watchlist, app Overview, Growth, History, Compare, follow/unfollow, and media rendering. Confirm zero console errors.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md docs/architecture.md docs/configuration.md docs/deploy.md
git commit -m "Document competitor intelligence operations"
```

- [ ] **Step 5: Push master and deploy to Dokku**

```bash
git push fork master
GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=15 -o IdentityAgent=none -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519' git push dokku-mantle master
```

Expected: migration 018 applies and Dokku healthchecks pass.

- [ ] **Step 6: Run the first watchlist baseline and verify counts**

After the mounted archive is writable, invoke `run_watchlist_job` once in the live container. Verify, without printing listing bodies or secrets:

```sql
select count(*) from discovery_watchlist where active;
select count(*) from discovery_listing_snapshots;
select count(*) from discovery_media_objects;
select count(*) from discovery_watchlist where last_error_code is not null;
```

Open the authenticated production pages and confirm at least one followed app has a baseline listing version and archived media. Growth attribution remains unavailable until subsequent dated observations and must render as unknown rather than zero.

---

## Self-Review

- Every approved requirement maps to a task: universal search (6), manual/automatic follow (2/5/6), immutable versions (4), archived media (4/7/9), app detail and comparison (7), daily scheduling (5), weekly in-dashboard summary (8), and responsive/security/reliability verification (6–9).
- Existing owned-app ASO storage is not migrated or overloaded.
- The plan contains no per-listing crawl of the full 24k-app index and no claim of competitor installs or revenue.
- Function names and table names are consistent across tasks.
- The first deployed increment is end-to-end usable and does not depend on the later owned-listing comparison increment.
