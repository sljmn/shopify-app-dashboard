# Owned ASO Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add vendor-independent Shopify App Store keyword analytics, merchant attribution, listing history, and keyword research to Mantle.

**Architecture:** Keep existing `ga4_daily` traffic summaries intact and add focused, app-scoped ASO tables populated by read-only GA4 queries. Persist GA4 capability discovery so unsupported fields cannot be mistaken for empty data, and enrich the existing customer model by app/domain. Fetch public listing and autocomplete data through separate idempotent collectors.

**Tech Stack:** Python 3.13, FastAPI, Jinja2, PostgreSQL/psycopg, Google Analytics Data API, APScheduler, httpx, Beautiful Soup, pytest, Playwright CLI.

---

## File Structure

- Create `src/app_dashboard/migrations/013_aso_intelligence.sql`: ASO storage, constraints, and indexes.
- Modify `src/app_dashboard/catalog.py`: listing locales and GA4 ASO configuration.
- Create `src/app_dashboard/aso_ga4.py`: metadata discovery, normalized GA4 requests, keyword and attribution upserts.
- Create `src/app_dashboard/aso.py`: report queries, filters, comparisons, and Opportunity score.
- Create `src/app_dashboard/listing_intelligence.py`: listing parsing, snapshot diffs, and autocomplete collection.
- Modify `src/app_dashboard/scheduler.py`: isolated daily ASO and listing jobs.
- Modify `src/app_dashboard/manual_sync.py`: include the new sources in fresh and full refreshes.
- Modify `src/app_dashboard/customers.py`: attribution joins and facets.
- Modify `src/app_dashboard/web.py`: ASO, CSV, and enriched customer routes.
- Modify `src/app_dashboard/metrics.py`: definitions for every new headline number.
- Create `src/app_dashboard/templates/aso.html`: portfolio and app-specific ASO views.
- Modify `src/app_dashboard/templates/base.html`: ASO navigation and responsive styles.
- Modify `src/app_dashboard/templates/customers.html`: source/keyword filters and columns.
- Modify `src/app_dashboard/templates/customer.html`: install-attribution block.
- Modify `pyproject.toml` and `uv.lock`: use Beautiful Soup for resilient HTML parsing.
- Create focused tests in `tests/test_aso_ga4.py`, `tests/test_aso.py`, and `tests/test_listing_intelligence.py`; extend existing catalog, migration, scheduler, manual-sync, customer, and web tests.

### Task 1: Create The ASO Schema

**Files:**
- Create: `src/app_dashboard/migrations/013_aso_intelligence.sql`
- Modify: `tests/test_migrations.py`

- [ ] **Step 1: Write the failing schema tests**

Extend the required app-owned tables and assert the important natural keys:

```python
ASO_TABLES = {
    "aso_source_capabilities", "aso_keyword_daily", "aso_install_sources",
    "aso_listing_snapshots", "aso_listing_changes",
}

def test_aso_tables_are_app_scoped(db):
    rows = db.execute(
        """select table_name, is_nullable from information_schema.columns
           where table_schema='public' and column_name='app_id'"""
    ).fetchall()
    required = {name for name, nullable in rows if nullable == "NO"}
    assert ASO_TABLES <= required

def test_keyword_daily_natural_key_is_unique(db, test_app):
    values = (test_app.id, "2026-08-11", "vat exemption", "en", "NL",
              "desktop", "search")
    sql = """insert into aso_keyword_daily
             (app_id, date, keyword, locale, country, device, search_type)
             values (%s,%s,%s,%s,%s,%s,%s)"""
    db.execute(sql, values)
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(sql, values)
```

Also add every table in `ASO_TABLES` to the exact `tables` set used by
`test_every_app_owned_table_has_a_required_app_id`; that existing test compares
the complete set rather than checking a subset.

- [ ] **Step 2: Run the migration tests and verify failure**

Run: `uv run --frozen pytest tests/test_migrations.py -q`

Expected: FAIL because the ASO tables do not exist.

- [ ] **Step 3: Add migration 013**

Create tables with these exact contracts:

```sql
create table aso_source_capabilities (
    app_id bigint not null references apps(id) on delete cascade,
    source text not null,
    status text not null check (status in ('ready','partial','unsupported','failed')),
    fields jsonb not null default '{}'::jsonb,
    checked_at timestamptz not null,
    error_code text,
    primary key (app_id, source)
);

create table aso_keyword_daily (
    app_id bigint not null references apps(id) on delete cascade,
    date date not null,
    keyword text not null,
    locale text not null default '', country text not null default '',
    device text not null default 'unknown', search_type text not null,
    users integer not null default 0 check (users >= 0),
    install_clicks integer not null default 0 check (install_clicks >= 0),
    average_position numeric(8,2), latest_position integer,
    position_samples integer not null default 0 check (position_samples >= 0),
    primary key (app_id, date, keyword, locale, country, device, search_type)
);

create index aso_keyword_app_date_idx on aso_keyword_daily (app_id, date desc);
create index aso_keyword_app_term_idx on aso_keyword_daily (app_id, keyword);

create table aso_install_sources (
    app_id bigint not null references apps(id) on delete cascade,
    attribution_key text not null,
    shop_domain text not null,
    shop_id text, installed_on date not null,
    source text not null, source_type text not null default '',
    source_value text not null default '', locale text not null default '',
    country text not null default '', device text not null default 'unknown',
    observed_at timestamptz not null default now(),
    primary key (app_id, attribution_key)
);
create index aso_install_app_shop_idx on aso_install_sources (app_id, shop_domain);
create index aso_install_app_date_idx on aso_install_sources (app_id, installed_on desc);

create table aso_listing_snapshots (
    id bigserial primary key,
    app_id bigint not null references apps(id) on delete cascade,
    locale text not null,
    captured_at timestamptz not null,
    content_hash text not null,
    listing jsonb not null,
    unique (app_id, locale, content_hash)
);
create index aso_listing_app_locale_time_idx
    on aso_listing_snapshots (app_id, locale, captured_at desc);

create table aso_listing_changes (
    id bigserial primary key,
    app_id bigint not null references apps(id) on delete cascade,
    snapshot_id bigint not null references aso_listing_snapshots(id) on delete cascade,
    locale text not null, changed_at timestamptz not null,
    field text not null, before_value jsonb, after_value jsonb,
    unique (snapshot_id, field)
);

create table aso_popular_keywords (
    keyword text primary key,
    source text not null check (source in ('autocomplete','manual')),
    first_seen_at timestamptz not null,
    last_seen_at timestamptz not null
);
```

- [ ] **Step 4: Run migration tests**

Run: `uv run --frozen pytest tests/test_migrations.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the schema**

```bash
git add src/app_dashboard/migrations/013_aso_intelligence.sql tests/test_migrations.py
git commit -m "Create app-scoped storage for owned ASO intelligence"
```

### Task 2: Configure Locales And Shared GA4 Access

**Files:**
- Modify: `src/app_dashboard/catalog.py`
- Modify: `src/app_dashboard/migrations/013_aso_intelligence.sql`
- Modify: `tests/test_catalog.py`
- Modify: `docs/configuration.md`

- [ ] **Step 1: Add failing catalog tests**

```python
def test_catalog_defaults_listing_locales_to_english(tmp_path):
    app = load_catalog(write_catalog(tmp_path, ga4=False), ENV)[0]
    assert app.listing_locales == ("en",)

def test_catalog_accepts_shared_credentials_and_multiple_locales(tmp_path):
    path = write_catalog(tmp_path, ga4={
        "property_id": "123", "credentials_env": "SHARED_GA4",
    }, listing_locales=["en", "de", "nl"])
    app = load_catalog(path, {**ENV, "SHARED_GA4": "{}"})[0]
    assert app.listing_locales == ("en", "de", "nl")

@pytest.mark.parametrize("locales", [[], ["en", "en"], ["English"], "en"])
def test_catalog_rejects_invalid_listing_locales(tmp_path, locales):
    with pytest.raises(CatalogError, match="listing_locales"):
        load_catalog(write_catalog(tmp_path, listing_locales=locales), ENV)
```

- [ ] **Step 2: Verify the tests fail**

Run: `uv run --frozen pytest tests/test_catalog.py -q`

Expected: FAIL because `listing_locales` is not part of `AppSpec`.

- [ ] **Step 3: Implement and persist locale configuration**

Add `listing_locales: tuple[str, ...]` to `AppSpec`, parse `listing_locales`
with `^[a-z]{2}(?:-[A-Z]{2})?$`, default to `("en",)`, and add this column to
migration 013:

```sql
alter table apps add column listing_locales jsonb not null default '["en"]'::jsonb;
```

Include it in `reconcile_catalog()` and `list_apps()`. Reusing the same
`credentials_env` value for multiple apps remains valid; do not duplicate the
secret in the database.

- [ ] **Step 4: Document the production shape and run tests**

Add this exact example to `docs/configuration.md`:

```yaml
listing_locales: [en, de, nl]
ga4:
  property_id: "123456789"
  credentials_env: SHARED_GA4_CREDENTIALS_JSON
```

Run: `uv run --frozen pytest tests/test_catalog.py tests/test_migrations.py -q`

Expected: PASS.

- [ ] **Step 5: Commit catalog support**

```bash
git add src/app_dashboard/catalog.py src/app_dashboard/migrations/013_aso_intelligence.sql tests/test_catalog.py docs/configuration.md
git commit -m "Allow apps to share GA4 access across listing locales"
```

### Task 3: Discover GA4 ASO Capabilities

**Files:**
- Create: `src/app_dashboard/aso_ga4.py`
- Create: `tests/test_aso_ga4.py`

- [ ] **Step 1: Write metadata discovery tests**

Use fake metadata objects to prove exact matching, partial support, and errors:

```python
def test_discovery_maps_only_dimensions_present_in_property():
    client = FakeClient(dimensions={
        "searchTerm", "customEvent:position", "customEvent:shop_url",
        "country", "language", "deviceCategory", "sessionDefaultChannelGroup",
    })
    result = discover_capabilities(client, "123")
    assert result.statuses == {
        "aso_keywords": "ready", "aso_attribution": "partial",
    }
    assert result.fields["keyword"] == "searchTerm"
    assert result.fields["position"] == "customEvent:position"
    assert result.fields["shop_domain"] == "customEvent:shop_url"

def test_discovery_marks_attribution_unsupported_without_shop_domain():
    result = discover_capabilities(FakeClient(dimensions={"searchTerm"}), "123")
    assert result.statuses["aso_keywords"] == "partial"
    assert result.statuses["aso_attribution"] == "unsupported"
    assert "shop_domain" in result.missing["aso_attribution"]

def test_discovery_records_a_sanitized_failure(db, test_app):
    sync_capabilities(db, RaisingClient("secret payload"), test_app)
    rows = db.execute(
        "select source, status, error_code from aso_source_capabilities order by source"
    ).fetchall()
    assert rows == [
        ("aso_attribution", "failed", "GoogleAPICallError"),
        ("aso_keywords", "failed", "GoogleAPICallError"),
    ]
```

- [ ] **Step 2: Verify failure**

Run: `uv run --frozen pytest tests/test_aso_ga4.py -q`

Expected: FAIL because `app_dashboard.aso_ga4` does not exist.

- [ ] **Step 3: Implement capability discovery**

Create immutable contracts and allowlisted candidates:

```python
FIELD_CANDIDATES = {
    "keyword": ("searchTerm", "customEvent:search_term", "customEvent:keyword"),
    "position": ("customEvent:position", "customEvent:search_position"),
    "shop_domain": ("customEvent:shop_url", "customEvent:shop_domain"),
    "shop_id": ("customEvent:shop_id",),
    "source": ("sessionSource", "firstUserSource", "customEvent:source"),
    "source_type": ("sessionMedium", "firstUserMedium", "customEvent:source_type"),
    "locale": ("language", "customEvent:locale"),
    "country": ("country",),
    "device": ("deviceCategory",),
    "search_type": ("customEvent:search_type", "sessionDefaultChannelGroup"),
}

@dataclass(frozen=True)
class CapabilityReport:
    statuses: dict[str, str]
    fields: dict[str, str]
    missing: dict[str, tuple[str, ...]]

def discover_capabilities(client, property_id: str) -> CapabilityReport:
    """Resolve allowlisted logical fields from property metadata."""

def sync_capabilities(conn, client, app: AppConfig) -> CapabilityReport:
    """Persist discovery status without recording secrets or raw errors."""
```

`discover_capabilities` calls `get_metadata(name=f"properties/{property_id}/metadata")`,
builds a set from `response.dimensions[*].api_name`, and selects the first
candidate for every logical field that exists in that set. Status is `ready`
for keyword reporting when `keyword` and `position` resolve, `partial` when only
`keyword` resolves, and `unsupported` without `keyword`. Attribution is `ready`
when `shop_domain` and `source` resolve, `partial` when only `shop_domain`
resolves, and `unsupported` without `shop_domain`. `sync_capabilities` upserts
separate `(app_id, source)` rows for `aso_keywords` and `aso_attribution`; it
catches Google API errors at this boundary, stores only the exception class
name in `error_code`, and marks both rows `failed`. Never log metadata values or
credentials.

- [ ] **Step 4: Run focused tests**

Run: `uv run --frozen pytest tests/test_aso_ga4.py -q`

Expected: PASS.

- [ ] **Step 5: Commit discovery**

```bash
git add src/app_dashboard/aso_ga4.py tests/test_aso_ga4.py
git commit -m "Detect GA4 fields before trusting ASO reports"
```

### Task 4: Import Keyword Rankings And Install Clicks

**Files:**
- Modify: `src/app_dashboard/aso_ga4.py`
- Modify: `tests/test_aso_ga4.py`

- [ ] **Step 1: Add failing fetch, pagination, merge, and upsert tests**

```python
def test_keyword_fetch_merges_traffic_and_install_clicks_by_dimensions():
    rows = fetch_keyword_rows(client_with_two_reports(), "123", FIELDS, START, END)
    assert rows == [{
        "date": date(2026, 8, 10), "keyword": "vat exemption", "locale": "en",
        "country": "NL", "device": "desktop", "search_type": "search",
        "users": 12, "install_clicks": 3, "average_position": Decimal("4.5"),
        "latest_position": 4, "position_samples": 2,
    }]

def test_keyword_fetch_reads_all_pages():
    assert len(fetch_keyword_rows(paged_client(100_001), "123", FIELDS, START, END)) == 100_001

def test_keyword_overlap_replaces_late_ga4_values(db, test_app):
    sync_aso_keywords(db, fake_client(users=2), test_app, today=TODAY)
    sync_aso_keywords(db, fake_client(users=5), test_app, today=TODAY)
    assert db.execute("select users from aso_keyword_daily").fetchone()[0] == 5

def test_keyword_fetch_retries_quota_errors_at_most_three_times():
    client = quota_then_success_client(failures=2)
    fetch_keyword_rows(client, "123", FIELDS, START, END, sleep=lambda _: None)
    assert client.calls == 3

def test_keyword_fetch_stops_after_third_quota_error():
    client = quota_then_success_client(failures=3)
    with pytest.raises(GoogleAPICallError):
        fetch_keyword_rows(client, "123", FIELDS, START, END, sleep=lambda _: None)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run --frozen pytest tests/test_aso_ga4.py -q`

Expected: FAIL for the missing fetch and sync functions.

- [ ] **Step 3: Implement normalized GA4 queries**

Add these interfaces:

```python
ASO_LOOKBACK_DAYS = 7
PAGE_SIZE = 100_000

def fetch_keyword_rows(client, property_id, fields, start, end,
                       sleep=time.sleep) -> list[dict]:
    """Return normalized rows merged on the complete ASO natural key."""

def upsert_keyword_rows(conn, app_id: int, rows: list[dict]) -> int:
    """Upsert normalized rows and return the number written."""

def sync_aso_keywords(conn, client, app: AppConfig, *, today=None,
                      earliest=None, force_full=False) -> int:
    """Replace the selected date window atomically and return its row count."""
```

Issue one traffic/position report and one install-click report using the same
allowlisted dimensions. Filter `install_clicks` to Shopify's `Add App button`
event only; `shopify_app_install` is a separate downstream install event and
must not be double-counted as an App Store listing click.
Merge by the full natural key. Use request `offset` and `limit` until
`offset >= row_count`. Delete and rewrite only the requested date range inside
one transaction so GA4 late-processing corrections also remove vanished rows.
Retry only quota and transient Google API errors, at most three attempts, with
delays of 1, 2, and 4 seconds; validation and permission failures are not
retried.

- [ ] **Step 4: Run keyword and existing traffic tests**

Run: `uv run --frozen pytest tests/test_aso_ga4.py tests/test_ga4.py -q`

Expected: PASS; existing `ga4_daily` behavior remains unchanged.

- [ ] **Step 5: Commit ingestion**

```bash
git add src/app_dashboard/aso_ga4.py tests/test_aso_ga4.py
git commit -m "Store reproducible Shopify keyword performance from GA4"
```

### Task 5: Build ASO Reports And Opportunity Scoring

**Files:**
- Create: `src/app_dashboard/aso.py`
- Create: `tests/test_aso.py`

- [ ] **Step 1: Write failing report tests**

Seed two apps and assert scope, prior-period comparisons, and totals:

```python
def test_keyword_report_groups_the_selected_period_and_previous_period(db, test_app):
    seed_keyword(db, test_app.id, "vat exemption", current_users=20,
                 current_clicks=4, current_position=8, prior_position=12)
    report = keyword_report(db, test_app.id, selection())
    row = report.rows[0]
    assert row.keyword == "vat exemption"
    assert row.position_change == 4
    assert row.conversion_pct == 20.0
    assert report.totals.users == 20

def test_opportunity_score_is_bounded_and_transparent():
    assert opportunity_score(clicks=0, latest_position=20) == 0
    assert opportunity_score(clicks=5, latest_position=1) == 0
    assert opportunity_score(clicks=5, latest_position=20) > 0
    assert 0 <= opportunity_score(clicks=999, latest_position=999) <= 100

def test_portfolio_report_keeps_unconfigured_apps_as_status_rows(db, apps):
    rows = portfolio_report(db, apps, selection())
    assert {row.app.slug for row in rows} == {app.slug for app in apps}

def test_position_history_is_chronological_and_app_scoped(db, apps):
    seed_keyword_history(db, apps[0].id, "vat exemption", [12, 9, 7])
    seed_keyword_history(db, apps[1].id, "vat exemption", [2])
    assert [point.position for point in position_history(
        db, apps[0].id, "vat exemption", selection()
    )] == [12, 9, 7]
```

- [ ] **Step 2: Verify failure**

Run: `uv run --frozen pytest tests/test_aso.py -q`

Expected: FAIL because `app_dashboard.aso` does not exist.

- [ ] **Step 3: Implement reports with grouped SQL**

Define:

```python
@dataclass(frozen=True)
class KeywordRow:
    keyword: str
    users: int
    install_clicks: int
    average_position: Decimal | None
    latest_position: int | None
    position_change: int | None
    conversion_pct: float
    opportunity_score: int

def opportunity_score(clicks: int, latest_position: int | None) -> int:
    if not clicks or latest_position is None or latest_position <= 1:
        return 0
    headroom = min(latest_position - 1, 49) / 49
    intent = min(clicks, 25) / 25
    return round(100 * headroom * intent)

def keyword_report(conn, app_id: int, period, facets=None):
    """Aggregate one app for the selected and immediately preceding period."""

def portfolio_report(conn, apps, period):
    """Return one row per app, including apps without supported data."""

def install_source_report(conn, app_id: int, period, facets=None):
    """Aggregate attributed installs using only allowlisted facets."""

def position_history(conn, app_id: int, keyword: str, period, facets=None):
    """Return chronological daily positions for one app and keyword."""
```

Use a fixed number of grouped queries, never one query per app or keyword. Treat
lower numeric position as better. Preserve `None` when no position exists.

- [ ] **Step 4: Run report tests**

Run: `uv run --frozen pytest tests/test_aso.py -q`

Expected: PASS.

- [ ] **Step 5: Commit reporting**

```bash
git add src/app_dashboard/aso.py tests/test_aso.py
git commit -m "Explain keyword growth opportunities from stored ASO data"
```

### Task 6: Ship The ASO Page And CSV Export

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/metrics.py`
- Modify: `src/app_dashboard/templates/base.html`
- Create: `src/app_dashboard/templates/aso.html`
- Modify: `tests/test_metrics.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing route and render tests**

```python
def test_aso_requires_login_and_portfolio_lists_every_app(db):
    assert signed_out().get("/aso", follow_redirects=False).status_code == 307
    body = unescape(_signed_in().get("/aso?period=30d").text)
    assert "ASO" in body and "Configuration" in body
    assert "Organic users" in body and "Install clicks" in body

def test_selected_aso_app_has_all_views_and_retains_filters(db):
    body = unescape(_signed_in().get(
        "/aso?app=test-app&period=30d&view=keywords&search_type=search"
    ).text)
    for label in ("Overview", "Keywords", "Install sources", "Listing changes", "Research"):
        assert label in body
    assert "app=test-app" in body and "search_type=search" in body

def test_aso_csv_is_scoped_and_safe(db):
    response = _signed_in().get("/aso.csv?app=test-app&period=30d")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.text.startswith("keyword,users,install_clicks")
```

- [ ] **Step 2: Verify failure**

Run: `uv run --frozen pytest tests/test_web.py tests/test_metrics.py -q`

Expected: FAIL because `/aso` does not exist.

- [ ] **Step 3: Add metric definitions and allowlisted filters**

Register `aso_organic_users`, `aso_keywords`, `aso_install_clicks`,
`aso_click_conversion`, `aso_average_position`, and `aso_movers`. Each definition
names `aso_keyword_daily` as its source and says whether lower or higher is
better.

In `web.py`, allowlist `view`, `search_type`, locale, country, device, sort, and
direction. Reuse `resolve_period()` for dates. Build all query strings with
`urlencode`, not Jinja concatenation of raw values.

- [ ] **Step 4: Create the page and CSV response**

Add `ASO` after `Traffic` in the sidebar. `aso.html` renders the all-app table
without an app selection and renders tabbed app views when selected. Use existing
cards, table-card, segmented controls, app picker, date picker, metric-definition
macro, and responsive table containment. In Keywords, selecting a row reveals
that keyword's chronological position history for the active filters; missing
daily positions remain gaps and are not interpolated.

Generate CSV with the standard library:

```python
buffer = io.StringIO(newline="")
writer = csv.writer(buffer)
writer.writerow(["keyword", "users", "install_clicks", "conversion_pct",
                 "average_position", "latest_position", "position_change",
                 "opportunity_score"])
for row in report.rows:
    writer.writerow([
        row.keyword, row.users, row.install_clicks, row.conversion_pct,
        row.average_position, row.latest_position, row.position_change,
        row.opportunity_score,
    ])
return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": "attachment; filename=aso-keywords.csv"})
```

- [ ] **Step 5: Run route tests and commit**

Run: `uv run --frozen pytest tests/test_web.py tests/test_metrics.py tests/test_aso.py -q`

Expected: PASS.

```bash
git add src/app_dashboard/web.py src/app_dashboard/metrics.py src/app_dashboard/templates/base.html src/app_dashboard/templates/aso.html tests/test_metrics.py tests/test_web.py
git commit -m "Expose owned ASO insights with scoped exports"
```

### Task 7: Schedule And Manually Refresh ASO Data

**Files:**
- Modify: `src/app_dashboard/scheduler.py`
- Modify: `src/app_dashboard/manual_sync.py`
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_manual_sync.py`

- [ ] **Step 1: Add failing isolation and source-list tests**

```python
def test_aso_job_isolates_apps(conn_factory, apps):
    results = run_aso_job(conn_factory, apps, settings, runner=fake_runner)
    assert [row["app"] for row in results] == [app.slug for app in configured_apps]
    assert results[1]["ok"] is False
    assert results[2]["ok"] is True

def test_manual_refresh_includes_aso_only_for_configured_apps(configured_app):
    assert ManualSyncCoordinator._sources(configured_app) == [
        "lifecycle", "transactions", "subscriptions", "ga4", "aso_keywords",
        "aso_attribution", "aso_listing",
    ]
```

- [ ] **Step 2: Verify failure**

Run: `uv run --frozen pytest tests/test_scheduler.py tests/test_manual_sync.py -q`

Expected: FAIL for missing ASO sources.

- [ ] **Step 3: Add isolated jobs and manual runners**

Add `run_aso_job()` using one GA4 client per configured app and call capability
discovery before keyword and attribution sync. Register it daily with a stable
job ID. Listing sync is also daily but runs for every app with `listing_url`,
regardless of GA4 configuration.

Extend `_run_source()`:

```python
elif source == "aso_keywords":
    sync_aso_keywords(conn, ga_client(), app, force_full=full_history)
elif source == "aso_attribution":
    sync_install_sources(conn, ga_client(), app, force_full=full_history)
elif source == "aso_listing":
    sync_listing(conn, app, http_get=httpx.get)
```

Do not let the existing hourly `ga4` traffic sync call ASO implicitly; separate
status and failure boundaries are part of the contract.

- [ ] **Step 4: Run scheduler/manual tests and commit**

Run: `uv run --frozen pytest tests/test_scheduler.py tests/test_manual_sync.py tests/test_ga4.py tests/test_aso_ga4.py -q`

Expected: PASS.

```bash
git add src/app_dashboard/scheduler.py src/app_dashboard/manual_sync.py tests/test_scheduler.py tests/test_manual_sync.py
git commit -m "Refresh ASO sources without delaying Shopify synchronization"
```

### Task 8: Import Merchant Install Attribution

**Files:**
- Modify: `src/app_dashboard/aso_ga4.py`
- Modify: `tests/test_aso_ga4.py`

- [ ] **Step 1: Write failing normalization and attribution tests**

```python
def test_shop_domains_are_normalized_before_attribution_keying():
    row = normalize_install_source({
        "shop": "HTTPS://Example.MyShopify.com/",
        "installed_on": "2026-08-11",
        "source": "App Store Search",
        "source_type": "keyword",
        "source_value": "vat exemption",
        "locale": "en", "country": "NL", "device": "mobile",
    })
    assert row["shop_domain"] == "example.myshopify.com"

def test_install_attribution_is_idempotent(db, test_app):
    client = attribution_client(shop="example.myshopify.com")
    assert sync_install_sources(db, client, test_app, today=TODAY) == 1
    assert sync_install_sources(db, client, test_app, today=TODAY) == 1
    assert db.execute("select count(*) from aso_install_sources").fetchone()[0] == 1

def test_missing_shop_dimension_is_unsupported_not_empty(db, test_app):
    with pytest.raises(UnsupportedAsoSource, match="shop_domain"):
        sync_install_sources(db, client, test_app, fields={"keyword": "searchTerm"})
```

- [ ] **Step 2: Verify failure**

Run: `uv run --frozen pytest tests/test_aso_ga4.py -q`

Expected: FAIL for missing attribution functions.

- [ ] **Step 3: Implement attribution sync**

Add:

```python
def normalize_shop_domain(raw: str) -> str:
    """Strip scheme, path, port, trailing dot and whitespace; then lowercase."""

def attribution_key(row: dict) -> str:
    """SHA-256 a JSON array of every normalized attribution field."""

def fetch_install_sources(client, property_id, fields, start, end) -> list[dict]:
    """Return normalized, paginated install-event rows."""

def sync_install_sources(conn, client, app, *, fields=None, today=None,
                         earliest=None, force_full=False) -> int:
    """Replace the seven-day or full attribution window atomically."""
```

Query only install events. Retain source, subtype, keyword/referrer, locale,
country, and device as separate fields. Use the same seven-day replacement
window and pagination rules as keyword sync. Empty successful results update
sync status; unsupported dimensions update capability status and do not delete
previous valid data.

- [ ] **Step 4: Run tests and commit**

Run: `uv run --frozen pytest tests/test_aso_ga4.py -q`

Expected: PASS.

```bash
git add src/app_dashboard/aso_ga4.py tests/test_aso_ga4.py
git commit -m "Preserve GA4 install attribution without inventing direct traffic"
```

### Task 9: Enrich Customers With Attribution

**Files:**
- Modify: `src/app_dashboard/customers.py`
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/customers.html`
- Modify: `src/app_dashboard/templates/customer.html`
- Modify: `tests/test_customers.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing customer-query tests**

```python
def test_customer_rows_join_latest_attribution_by_app_and_domain(db, apps):
    seed_same_domain_in_two_apps(db, apps)
    rows = list_customers(db, source="App Store Search")
    assert [(r.app_slug, r.attribution_source) for r in rows] == [
        ("alpha", "App Store Search")
    ]

def test_customer_detail_distinguishes_missing_attribution(db, shop):
    detail = customer_detail(db, shop.gid)
    assert detail["apps"][0]["attribution"] is None
```

- [ ] **Step 2: Verify failure**

Run: `uv run --frozen pytest tests/test_customers.py tests/test_web.py -q`

Expected: FAIL because customer models have no attribution.

- [ ] **Step 3: Extend grouped customer queries**

Use one lateral subquery selecting the newest `aso_install_sources` row by
`(app_id, lower(shop_domain))`. Add allowlisted `source` and `keyword` filters,
facet values, result fields, count-query predicates, and retained pager params.
Do not execute a query per customer.

- [ ] **Step 4: Render customer attribution**

Add Source and Keyword columns to Customers. Add source select and keyword
search controls. On each app section of `customer.html`, show an unframed
`Install attribution` section containing date, source, subtype, keyword/referrer,
locale, country, and device. When the joined value is `None`, render exactly:
`No attribution found in the connected GA4 property.`

- [ ] **Step 5: Run customer/web tests and commit**

Run: `uv run --frozen pytest tests/test_customers.py tests/test_web.py -q`

Expected: PASS.

```bash
git add src/app_dashboard/customers.py src/app_dashboard/web.py src/app_dashboard/templates/customers.html src/app_dashboard/templates/customer.html tests/test_customers.py tests/test_web.py
git commit -m "Join marketing attribution to the merchant workflow"
```

### Task 10: Capture And Diff Localized Listings

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/app_dashboard/listing_intelligence.py`
- Create: `tests/fixtures/shopify_listing.html`
- Create: `tests/test_listing_intelligence.py`

- [ ] **Step 1: Add the parser dependency**

Run: `uv add beautifulsoup4`

Expected: `pyproject.toml` and `uv.lock` contain Beautiful Soup.

- [ ] **Step 2: Write failing fixture-based parser and diff tests**

```python
def test_listing_parser_extracts_stable_normalized_fields():
    listing = parse_listing(FIXTURE.read_text())
    assert listing["name"] == "VAT / TAX Exemption"
    assert listing["description"].startswith("Validate EU VAT")
    assert listing["screenshots"] == ["https://cdn.shopify.com/example-1.png"]

def test_second_identical_snapshot_creates_no_row(db, test_app):
    first = store_listing_snapshot(db, test_app.id, "en", LISTING, NOW)
    second = store_listing_snapshot(db, test_app.id, "en", LISTING, LATER)
    assert second.snapshot_id == first.snapshot_id
    assert db.execute("select count(*) from aso_listing_changes").fetchone()[0] == 0

def test_changed_fields_create_before_after_rows(db, test_app):
    store_listing_snapshot(db, test_app.id, "en", {"name": "Old"}, NOW)
    store_listing_snapshot(db, test_app.id, "en", {"name": "New"}, LATER)
    assert db.execute(
        "select field, before_value, after_value from aso_listing_changes"
    ).fetchone() == ("name", "Old", "New")

def test_failed_listing_fetch_retains_last_snapshot_and_marks_stale(db, test_app):
    store_listing_snapshot(db, test_app.id, "en", LISTING, NOW)
    result = sync_listing(db, test_app, http_get=raising_get, now=LATER)
    assert result["status"] == "failed"
    assert db.execute("select count(*) from aso_listing_snapshots").fetchone()[0] == 1
```

- [ ] **Step 3: Implement normalized parsing and storage**

Define:

```python
LISTING_FIELDS = ("name", "description", "features", "pricing",
                  "icon", "screenshots", "rating", "rating_count")

@dataclass(frozen=True)
class SnapshotResult:
    snapshot_id: int
    created: bool
    changed_fields: tuple[str, ...]

def parse_listing(html: str) -> dict:
    """Extract and normalize every field in LISTING_FIELDS."""

def listing_hash(listing: dict) -> str:
    """SHA-256 canonical JSON with sorted keys and compact separators."""

def store_listing_snapshot(conn, app_id: int, locale: str,
                           listing: dict, captured_at) -> SnapshotResult:
    """Reuse identical content or insert a snapshot plus field-level diffs."""

def sync_listing(conn, app: AppConfig, http_get=httpx.get, now=None) -> dict:
    """Fetch and store every configured locale with bounded timeouts."""
```

Prefer the page's `SoftwareApplication` JSON-LD for name, description, image,
brand, and rating. Parse feature, pricing, and screenshot sections from the DOM
fixture with Beautiful Soup. Normalize whitespace and absolute HTTPS URLs before
hashing canonical JSON. Request `listing_url?locale=<locale>` for each configured
locale with a 15-second timeout and `User-Agent: Mantle ASO Intelligence/1.0`.
Retry HTTP 429, 502, 503, and 504 responses at most three times with 1, 2, and
4 second delays. On final failure, update source status and retain the last
valid snapshot unchanged.

- [ ] **Step 4: Run parser tests and commit**

Run: `uv run --frozen pytest tests/test_listing_intelligence.py -q`

Expected: PASS.

```bash
git add pyproject.toml uv.lock src/app_dashboard/listing_intelligence.py tests/fixtures/shopify_listing.html tests/test_listing_intelligence.py
git commit -m "Track meaningful changes to localized Shopify listings"
```

### Task 11: Collect Public Keyword Research

**Files:**
- Modify: `src/app_dashboard/listing_intelligence.py`
- Modify: `src/app_dashboard/aso.py`
- Modify: `src/app_dashboard/templates/aso.html`
- Modify: `tests/test_listing_intelligence.py`
- Modify: `tests/test_aso.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing autocomplete and cross-reference tests**

```python
def test_autocomplete_reads_only_search_phrases():
    payload = {"searches": [{"name": "email popup", "target": "/search?q=x"}],
               "apps": [{"name": "Not a keyword"}]}
    assert parse_autocomplete(payload) == ["email popup"]

def test_keyword_research_marks_listing_and_traffic_matches(db, test_app):
    seed_popular(db, "vat exemption", "invoice generator")
    seed_listing(db, test_app.id, description="Automatic VAT exemption")
    seed_keyword(db, test_app.id, "vat exemption")
    rows = keyword_research(db, test_app.id)
    assert rows[0].in_listing is True and rows[0].in_traffic is True
```

- [ ] **Step 2: Verify failure**

Run: `uv run --frozen pytest tests/test_listing_intelligence.py tests/test_aso.py -q`

Expected: FAIL for missing autocomplete and research functions.

- [ ] **Step 3: Implement bounded autocomplete discovery**

Call the verified public endpoint:

```python
AUTOCOMPLETE_URL = "https://apps.shopify.com/search/autocomplete"

def parse_autocomplete(payload: dict) -> list[str]:
    return sorted({item["name"].strip().casefold()
                   for item in payload.get("searches", []) if item.get("name")})

def sync_popular_keywords(conn, seeds: Sequence[str], http_get=httpx.get,
                          now=None) -> int:
    """Fetch at most 100 seeds and return the number of keyword upserts."""
```

Derive bounded seeds from words and two-word phrases in current listing titles,
descriptions, and observed GA4 keywords. Limit to 100 unique seeds per daily
run, sleep between requests, and upsert first/last seen timestamps. Record the
global completion time in `operations_state` under `aso_popular_keywords`.

- [ ] **Step 4: Add the Research view**

`keyword_research()` returns keyword, source, first/last seen, `in_listing`, and
`in_traffic`. Render a searchable, sortable table in the existing Research tab
and preserve app/period/filter parameters.

- [ ] **Step 5: Run tests and commit**

Run: `uv run --frozen pytest tests/test_listing_intelligence.py tests/test_aso.py tests/test_web.py -q`

Expected: PASS.

```bash
git add src/app_dashboard/listing_intelligence.py src/app_dashboard/aso.py src/app_dashboard/templates/aso.html tests/test_listing_intelligence.py tests/test_aso.py tests/test_web.py
git commit -m "Turn Shopify autocomplete into owned keyword research"
```

### Task 12: Render Listing History And Complete Operations

**Files:**
- Modify: `src/app_dashboard/aso.py`
- Modify: `src/app_dashboard/templates/aso.html`
- Modify: `src/app_dashboard/scheduler.py`
- Modify: `src/app_dashboard/manual_sync.py`
- Modify: `src/app_dashboard/templates/overview.html`
- Modify: `README.md`
- Modify: `docs/configuration.md`
- Modify: `tests/test_aso.py`
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_manual_sync.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Add failing history and status tests**

```python
def test_listing_history_returns_only_real_changes(db, test_app):
    rows = listing_history(db, test_app.id, locale="en")
    assert [row.field for row in rows] == ["description", "screenshots"]
    assert rows[0].keyword_movement == {"improved": 2, "declined": 1}

def test_aso_page_distinguishes_empty_unsupported_failed_and_stale(db):
    for status in ("ready", "partial", "unsupported", "failed"):
        seed_capability(db, status)
        assert STATUS_COPY[status] in _signed_in().get("/aso?app=test-app").text
```

- [ ] **Step 2: Verify failure**

Run: `uv run --frozen pytest tests/test_aso.py tests/test_web.py -q`

Expected: FAIL until history and status presentation exist.

- [ ] **Step 3: Finish listing and operational UI**

Render the current localized listing and field-level before/after timeline in
the Listing changes tab. For every change timestamp, aggregate keyword position
movement from the seven days before versus the seven days after into improved,
declined, and unchanged counts. Label this `Observed keyword movement` and state
that it is correlation, not causation. Add a source-status band showing last
successful sync, capability status, stale state, and sanitized error code for
keywords, attribution, listing, and research. The Overview fetch menu
descriptions must say that fresh/all refreshes include configured ASO sources.

Register the global autocomplete job once daily after listing jobs. It must use
`operations_state`, not an arbitrary app's `sync_state` row.

- [ ] **Step 4: Document setup and limitations**

Add exact service-account instructions, required Analytics Viewer role,
per-app property IDs, locale configuration, backfill procedure, data-retention
limit, and the distinction between Partner installs and GA4 attribution to
`README.md` and `docs/configuration.md`.

- [ ] **Step 5: Run the full suite**

Run: `uv run --frozen pytest -q`

Expected: all tests pass with no new warnings.

- [ ] **Step 6: Browser verification**

Start a local server using the existing test database and open it with the
Playwright CLI. Verify at 1440x900 and 390x844:

1. All-app ASO portfolio.
2. Selected-app Keywords view with filters and sorting.
3. Install sources table.
4. Listing before/after history.
5. Research table.
6. Customer filters and customer-detail attribution.
7. No global horizontal overflow; wide tables scroll only inside `.table-card`.
8. No console errors and every tab/control remains keyboard reachable.

- [ ] **Step 7: Commit the completed product surface**

```bash
git add src/app_dashboard README.md docs/configuration.md tests
git commit -m "Complete owned ASO intelligence across apps and merchants"
```

### Task 13: Configure, Backfill, Audit, And Deploy

**Files:**
- Modify: `config/apps.yml`
- Modify: `docs/configuration.md`

- [ ] **Step 1: Grant and configure production access**

Create one Google service account, grant its email the Viewer role on every GA4
property, set `SHARED_GA4_CREDENTIALS_JSON` in Dokku, and add each verified
property ID plus listing locales to `config/apps.yml`. Never commit the JSON.

- [ ] **Step 2: Run configuration and full tests**

Run:

```bash
uv run --frozen pytest tests/test_catalog.py tests/test_aso_ga4.py -q
uv run --frozen pytest -q
```

Expected: PASS.

- [ ] **Step 3: Commit only non-secret configuration**

```bash
git add config/apps.yml docs/configuration.md
git commit -m "Map Shopify apps to their ASO data properties"
```

- [ ] **Step 4: Push master and deploy to Dokku**

```bash
git push fork master
git push dokku-mantle HEAD:master
```

Expected: Dokku health checks succeed and `https://mantle.newcraft.dev/healthz`
returns `{"status":"ok"}`.

- [ ] **Step 5: Run one full refresh and audit coverage**

Use `Fetch all data again`, wait for every source to finish, then record per app:

- GA4 compatibility fields found/missing;
- earliest/latest keyword date;
- Partner installs versus attributed installs;
- attribution coverage percentage;
- listing locales captured;
- keyword and change row counts.

Do not label the feature complete for an app whose compatibility state is
`failed`; fix access or configuration and rerun that app.
