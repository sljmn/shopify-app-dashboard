# Multi-App Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one independently maintained dashboard that aggregates all configured Shopify apps and can narrow every supported report to one app.

**Architecture:** Add a YAML-backed organization/app catalog and make `app_id` mandatory in every lifecycle, financial, usage, traffic, annotation, and sync table. Pass an explicit app through every write path, use an optional report scope in read paths, and expose that scope through a global `?app=<slug>` selector. Start from a new database and replay each app's Partner history instead of preserving the single-app data layout.

**Tech Stack:** Python 3.13, FastAPI, psycopg/Postgres, Pydantic, PyYAML, APScheduler, Jinja2, pytest, Playwright CLI.

---

## File Map

- `config/apps.yml`: committed non-secret catalog for the 16 apps and five organizations.
- `src/app_dashboard/catalog.py`: parse, validate, reconcile, and query configured organizations/apps.
- `src/app_dashboard/scope.py`: the small trusted SQL predicate helper shared by report modules.
- `src/app_dashboard/migrations/011_multi_app.sql`: breaking empty-database migration and app-scoped keys.
- `src/app_dashboard/config.py`: dashboard/auth/operational settings only; singular app settings move to the catalog.
- `src/app_dashboard/ingest_raw.py`, `shops.py`, `derive.py`: app-scoped writes and replay.
- `src/app_dashboard/pipeline.py`, `scheduler.py`, `partner_api.py`: per-app cursors and organization-aware coordination.
- `src/app_dashboard/stats.py`, `customers.py`, `usage.py`, `annotations.py`, `ga4.py`: optional or required app scope, depending on the report.
- `src/app_dashboard/web.py`, `templates/base.html`, page templates: global app selector and preserved scope.
- `src/app_dashboard/export.py`, `markdown_export.py`: scoped exports with app identity on rows.
- `src/app_dashboard/ops.py`, `slack.py`, `digest.py`: app-labeled health, alerts, and digest data.
- `scripts/seed_demo.py`, `scripts/check_invariants.py`: multi-app demo world and per-app plus combined invariants.
- `tests/conftest.py`, existing test modules, and new focused tests: two-app fixtures and isolation coverage.

## Task 1: Catalog And Breaking Schema

**Files:**
- Create: `config/apps.yml`
- Create: `src/app_dashboard/catalog.py`
- Create: `src/app_dashboard/migrations/011_multi_app.sql`
- Create: `tests/test_catalog.py`
- Modify: `pyproject.toml`
- Modify: `src/app_dashboard/config.py`
- Modify: `src/app_dashboard/migrate.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_config.py`
- Test: `tests/test_migrations.py`

- [ ] **Step 1: Write catalog validation tests**

Add tests that load a temporary YAML file and assert two organizations can contain apps with independent annual prices and usage settings. Add failures for duplicate slugs, duplicate Partner App GIDs, a missing token env key, malformed annual prices, partial GA4 configuration, and activation/live event names outside the allowlist.

Use this complete minimal fixture shape:

```yaml
organizations:
  - partner_org_id: "1"
    name: Org One
    token_env: TOKEN_ORG_ONE
    apps:
      - slug: alpha
        name: Alpha
        partner_app_id: gid://partners/App/101
        annual_plan_amounts: ["190.00"]
        usage:
          token_env: ALPHA_USAGE_TOKEN
          event_types: [configured, rendered]
          activation_event: configured
          live_event: rendered
  - partner_org_id: "2"
    name: Org Two
    token_env: TOKEN_ORG_TWO
    apps:
      - slug: beta
        name: Beta
        partner_app_id: gid://partners/App/202
        annual_plan_amounts: []
```

- [ ] **Step 2: Run the focused tests and verify collection fails**

Run:

```bash
uv run pytest tests/test_catalog.py tests/test_config.py -q
```

Expected: failure because `app_dashboard.catalog` and `APPS_CONFIG_PATH` do not exist.

- [ ] **Step 3: Add PyYAML and implement typed catalog loading**

Add `pyyaml` as a direct dependency. In `catalog.py`, define immutable `OrganizationConfig` and `AppConfig` dataclasses plus `load_catalog(path, environ=os.environ)`. Parse YAML with `yaml.safe_load`, normalize every annual amount to `Decimal`, resolve token values from the named envvars, and raise one `CatalogError` carrying an actionable message.

Keep unpersisted YAML values separate from runtime apps. `AppSpec` is returned by the parser; only reconciliation may construct an `AppConfig` with required database IDs:

```python
@dataclass(frozen=True, kw_only=True)
class AppSpec:
    slug: str
    name: str
    partner_app_id: str
    partner_org_id: str
    partner_token: str
    annual_plan_amounts: frozenset[Decimal]
    listing_url: str | None
    usage_token: str | None
    usage_event_types: frozenset[str]
    usage_activation_event: str | None
    usage_live_event: str | None
    ga4_property_id: str | None
    ga4_credentials_json: str | None
    active: bool = True

@dataclass(frozen=True, kw_only=True)
class AppConfig(AppSpec):
    id: int
    organization_id: int
```

Expose the exact functions `load_catalog(path: str | Path, environ: Mapping[str, str] = os.environ) -> list[AppSpec]`, `reconcile_catalog(conn, configured: list[AppSpec]) -> list[AppConfig]`, `list_apps(conn, environ: Mapping[str, str] = os.environ, *, active_only: bool = True) -> list[AppConfig]`, and `app_by_slug(conn, slug: str, environ: Mapping[str, str] = os.environ) -> AppConfig | None`.

- [ ] **Step 4: Reduce global Settings to dashboard-wide values**

Remove singular `partner_api_token`, `partner_org_id`, `partner_app_id`, `app_name`, `app_slug`, `app_listing_url`, `annual_plan_amounts`, GA4, and usage fields from `Settings`. Add:

```python
apps_config_path: str = "config/apps.yml"
dashboard_name: str = "Shopify Apps Analytics"
```

Keep auth, Slack, scheduler, dates, and proxy settings global. Move every removed validator to `catalog.py` and update `tests/test_config.py` to prove Settings no longer requires singular app credentials.

- [ ] **Step 5: Write the breaking migration test**

In `tests/test_migrations.py`, assert migration 011 fails with a message containing `new empty database` when any app-owned table has rows. On an empty database, assert all ten tables have a non-null `app_id`, and assert these duplicate pairs can coexist:

```sql
insert into shops(app_id, shop_gid, install_state)
values (1, 'gid://shopify/Shop/1', 'installed'),
       (2, 'gid://shopify/Shop/1', 'installed');
```

Insert organizations and apps with IDs 1 and 2 before these shop rows so the foreign keys are exercised rather than disabled.

- [ ] **Step 6: Implement migration 011**

The migration must:

1. Refuse non-empty app-owned tables.
2. Create `organizations` and `apps`.
3. Add required `app_id` foreign keys.
4. Replace global string primary/unique keys with app-scoped keys.
5. Add indexes beginning with `app_id` for all common read paths.
6. Add `operations_state(source text primary key, last_run_at timestamptz)` for the one global digest marker that does not belong to an app.

Use these core tables:

```sql
create table organizations (
    id bigserial primary key,
    partner_org_id text not null unique,
    name text not null,
    token_env text not null,
    active boolean not null default true
);

create table apps (
    id bigserial primary key,
    organization_id bigint not null references organizations(id),
    slug text not null unique,
    name text not null,
    partner_app_id text not null unique,
    annual_plan_amounts jsonb not null default '[]'::jsonb,
    listing_url text,
    usage_token_env text,
    usage_event_types jsonb not null default '[]'::jsonb,
    usage_activation_event text,
    usage_live_event text,
    ga4_property_id text,
    ga4_credentials_env text,
    active boolean not null default true
);
```

Keep generated numeric IDs globally unique, but change string identities to composite uniqueness, including `(app_id, id)` for charges, subscriptions, raw events, and transactions and `(app_id, shop_gid)` for shops.

- [ ] **Step 7: Reconcile catalog during migration**

Update `migrate.main()` to run migrations, load `Settings.apps_config_path`, reconcile the catalog, and close the connection. A database with migrations but no reconciled apps must not be considered ready.

- [ ] **Step 8: Commit the catalog foundation**

Run:

```bash
uv lock
uv run pytest tests/test_catalog.py tests/test_config.py tests/test_migrations.py -q
git add pyproject.toml uv.lock config/apps.yml src/app_dashboard/catalog.py src/app_dashboard/config.py src/app_dashboard/migrate.py src/app_dashboard/migrations/011_multi_app.sql tests/conftest.py tests/test_catalog.py tests/test_config.py tests/test_migrations.py
git commit -m "feat: add multi-app catalog and ownership schema"
```

Expected: focused tests pass.

## Task 2: App-Scoped Ingest And Derivation

**Files:**
- Modify: `src/app_dashboard/ingest_raw.py`
- Modify: `src/app_dashboard/shops.py`
- Modify: `src/app_dashboard/derive.py`
- Modify: `tests/test_ingest_raw.py`
- Modify: `tests/test_shops.py`
- Modify: `tests/test_derive.py`
- Modify: `tests/test_invariants.py`

- [ ] **Step 1: Add cross-app collision regression tests**

Use two fixture apps with the same `shop_gid`, charge GID, subscription GID, raw platform event ID, and transaction ID. Assert both apps retain independent rows and different MRR. Add a case where `$190` is annual in app A and monthly in app B.

The intended calls are:

```python
upsert_raw_events(db, alpha, [event])
upsert_charges(db, alpha, [event])
upsert_transactions(db, alpha, [transaction])
derive_installation(db, alpha.id, event["shop_gid"])
```

- [ ] **Step 2: Verify the new tests fail against global keys**

Run:

```bash
uv run pytest tests/test_ingest_raw.py tests/test_shops.py tests/test_derive.py -q
```

Expected: duplicate-key or wrong-amount failures.

- [ ] **Step 3: Scope every ingest write**

Change signatures to accept `AppConfig` and write `app.id` explicitly: `upsert_raw_events(conn, app: AppConfig, events: list[dict]) -> int`, `upsert_transactions(conn, app: AppConfig, rows: list[dict]) -> int`, `upsert_charges(conn, app: AppConfig, events: list[dict]) -> int`, and `plan_interval_for(amount, annual_amounts: frozenset[Decimal]) -> str`.

Every `ON CONFLICT` target must start with `app_id`. Do not read annual prices from global settings or module-level state.

- [ ] **Step 4: Scope shop state and derivation**

Change `upsert_shop_state` to require `app_id`. Use the exact derivation signatures `derive_installation(conn, app_id: int, shop_gid: str) -> list[str]` and `derive_installations(conn, app_id: int, shop_gids) -> dict[str, int]`, with `(app_id, shop_gid)` and `(app_id, charge_gid)` throughout.

Include `app_id` in derived-event inserts, subscription upserts, charge lookups, snapshots, and new-event counts.

- [ ] **Step 5: Make the invariant world contain two apps**

Update `tests/test_invariants.py` so its seeded world includes the same shop in both apps. Preserve all ingest/derive idempotence assertions per app. The aggregate arithmetic assertion belongs in Task 4, after report scopes exist, so this task still ends green.

- [ ] **Step 6: Commit app-owned write paths**

Run:

```bash
uv run pytest tests/test_ingest_raw.py tests/test_shops.py tests/test_derive.py -q
git add src/app_dashboard/ingest_raw.py src/app_dashboard/shops.py src/app_dashboard/derive.py tests/test_ingest_raw.py tests/test_shops.py tests/test_derive.py tests/test_invariants.py
git commit -m "feat: isolate ingest and derivation by app"
```

## Task 3: Multi-App Pipeline And Scheduler

**Files:**
- Modify: `src/app_dashboard/pipeline.py`
- Modify: `src/app_dashboard/scheduler.py`
- Modify: `src/app_dashboard/slack.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_slack.py`

- [ ] **Step 1: Test per-app cursors and failure isolation**

Add tests where alpha sync succeeds, beta raises, and gamma still runs. Assert alpha and gamma update only their own `(app_id, source)` rows and beta does not advance. Assert one `PartnerClient` is created per organization, not per app.

- [ ] **Step 2: Make pipeline functions app-explicit**

Use the exact signatures `run_sync(conn, client, app: AppConfig, settings, http_post) -> dict` and `sync_transactions(conn, client, app: AppConfig, settings, sleep=time.sleep) -> dict`.

Filter transaction lookback, event snapshots, alerts, and `sync_state` by `app.id`. Return the app slug in each summary.

- [ ] **Step 3: Implement the coordinator**

Add:

```python
def configured_clients(apps: list[AppConfig]) -> dict[str, PartnerClient]:
    return {
        org_id: PartnerClient(group[0].partner_token, org_id)
        for org_id, group in groupby_apps_by_org(apps).items()
    }

def run_all_apps(conn_factory, apps, settings, sync_one) -> list[dict]:
    results = []
    clients = configured_clients(apps)
    for app in apps:
        try:
            results.append(sync_one(conn_factory, clients[app.partner_org_id], app, settings))
        except Exception as exc:
            logger.exception("%s sync failed", app.slug)
            results.append({"app": app.slug, "ok": False, "error": str(exc)})
    return results
```

Use one scheduled job for all event feeds, one for all transaction feeds, and one for all configured GA4 apps. Keep the single scheduler instance.

- [ ] **Step 4: Label Slack events by app**

Load shops by `(app_id, shop_gid)`, include `app.name` in event messages, and link back with `?app=<slug>`. Deduplication remains per sync-state incident and therefore becomes app-scoped.

- [ ] **Step 5: Commit synchronization**

Run:

```bash
uv run pytest tests/test_pipeline.py tests/test_scheduler.py tests/test_slack.py -q
git add src/app_dashboard/pipeline.py src/app_dashboard/scheduler.py src/app_dashboard/slack.py tests/test_pipeline.py tests/test_scheduler.py tests/test_slack.py
git commit -m "feat: coordinate syncs across partner apps"
```

## Task 4: Shared Report Scope And Aggregate Metrics

**Files:**
- Create: `src/app_dashboard/scope.py`
- Create: `tests/test_scope.py`
- Modify: `src/app_dashboard/stats.py`
- Modify: `tests/test_stats.py`
- Modify: `tests/test_invariants.py`

- [ ] **Step 1: Test a trusted scope predicate**

Define tests for:

```python
Scope.all().predicate("sub") == ("true", ())
Scope.for_app(7).predicate("sub") == ("sub.app_id = %s", (7,))
```

The alias must match `^[a-z_][a-z0-9_]*$`; reject caller-provided SQL punctuation.

- [ ] **Step 2: Implement `Scope`**

```python
@dataclass(frozen=True)
class Scope:
    app_id: int | None = None

    @classmethod
    def all(cls) -> "Scope":
        return cls()

    @classmethod
    def for_app(cls, app_id: int) -> "Scope":
        return cls(app_id=app_id)

    def predicate(self, alias: str | None = None) -> tuple[str, tuple]:
        column = f"{alias}.app_id" if alias else "app_id"
        return ("true", ()) if self.app_id is None else (f"{column} = %s", (self.app_id,))
```

- [ ] **Step 3: Add optional scope to every comparable stat**

Every lifecycle/financial function accepts `scope: Scope = Scope.all()`. Apply the predicate to all tables and every correlated subquery. Joins on string IDs must join both `app_id` and the string key:

```sql
join shops sh
  on sh.app_id = sub.app_id
 and sh.shop_gid = sub.shop_gid
```

Update Overview, comparison, MRR trend/movements, revenue, countries, plan mix, churn, actions, activity, funnel, and retention. Add `app_id` and `app_slug` to row-returning queries where the combined UI needs labels.

- [ ] **Step 4: Keep traffic scope required**

Change traffic functions to require `app_id: int` rather than accepting `Scope.all()`. A combined call must raise `ValueError("Traffic requires one selected app")`.

- [ ] **Step 5: Prove aggregate arithmetic**

Add tests for two apps with overlapping shop IDs. Assert all-app MRR, installed count, paying count, collected revenue, monthly MRR buckets, and funnel counts equal the sum of per-app results. Assert annual price and correlated subqueries never cross app boundaries.

Add the combined invariant with the final scoped interface:

```python
assert stats.overview_stats(world, Scope.all())["active_mrr"] == (
    stats.overview_stats(world, Scope.for_app(alpha.id))["active_mrr"]
    + stats.overview_stats(world, Scope.for_app(beta.id))["active_mrr"]
)
```

- [ ] **Step 6: Commit scoped statistics**

Run:

```bash
uv run pytest tests/test_scope.py tests/test_stats.py tests/test_invariants.py -q
git add src/app_dashboard/scope.py src/app_dashboard/stats.py tests/test_scope.py tests/test_stats.py tests/test_invariants.py
git commit -m "feat: aggregate and filter metrics by app"
```

## Task 5: Customers And Cross-App Merchant Detail

**Files:**
- Modify: `src/app_dashboard/customers.py`
- Modify: `tests/test_customers.py`
- Modify: `src/app_dashboard/templates/customers.html`
- Modify: `src/app_dashboard/templates/customer.html`

- [ ] **Step 1: Write customer isolation tests**

Seed one `shop_gid` in alpha and beta plus a shop in alpha only. Assert combined list has three app-installation rows, selected alpha has two, and detail for the shared shop returns two app sections with independent lifecycle and transactions.

- [ ] **Step 2: Scope list, count, and facets**

Add `scope: Scope` to `list_customers`, `count_customers`, and `distinct_facets`. Include `a.slug AS app_slug` and `a.name AS app_name`; add app name to search and sort behavior in combined scope.

- [ ] **Step 3: Replace domain-only detail identity**

Use the stable shop GID in the route identity and group all matching app installations with `customer_detail(conn, shop_gid: str, scope: Scope = Scope.all()) -> dict | None`.

Return a dictionary with `shop_gid`, `display_name`, `domains`, and `apps: list[dict]`, where each app section contains its own current state, subscription, lifecycle, payments, and usage.

- [ ] **Step 4: Update templates**

Add an App column in combined Customers, retain it in narrow/mobile layouts, and render merchant detail as unframed app sections with app headings. Do not nest cards.

- [ ] **Step 5: Commit customer views**

Run:

```bash
uv run pytest tests/test_customers.py -q
git add src/app_dashboard/customers.py src/app_dashboard/templates/customers.html src/app_dashboard/templates/customer.html tests/test_customers.py
git commit -m "feat: show app ownership across customer views"
```

## Task 6: Global App Selector And Scoped Routes

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `src/app_dashboard/templates/overview.html`
- Modify: `src/app_dashboard/templates/actions.html`
- Modify: `src/app_dashboard/templates/churn.html`
- Modify: `src/app_dashboard/templates/retention.html`
- Modify: `src/app_dashboard/templates/funnel.html`
- Modify: `src/app_dashboard/templates/traffic.html`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Test scope resolution and preservation**

Add requests for no `app`, valid `?app=alpha`, inactive app, and unknown app. Assert no app means all apps, valid app narrows data, inactive/unknown returns 404, and pagination/form/link URLs retain `app=alpha`.

- [ ] **Step 2: Add a request scope dependency**

Inside `create_app`, load active apps once for the process lifetime and expose:

```python
catalog_conn = conn_factory()
try:
    active_apps = list_apps(catalog_conn)
finally:
    catalog_conn.close()
apps_by_slug = {app.slug: app for app in active_apps}

def resolve_scope(request: Request) -> tuple[Scope, AppConfig | None]:
    slug = request.query_params.get("app")
    if not slug:
        return Scope.all(), None
    app = apps_by_slug.get(slug)
    if app is None:
        raise HTTPException(status_code=404, detail="Unknown app")
    return Scope.for_app(app.id), app
```

The catalog is reconciled before server startup and configuration changes require a restart, so an immutable in-memory lookup is intentional. Do not retain a database connection globally.

- [ ] **Step 3: Thread scope through every route**

Overview, Customers, Actions, Funnel, Churn, Retention, FAQ context, Markdown, and JSON receive the resolved scope. Traffic and activation return a chooser state when no app is selected rather than querying combined data.

- [ ] **Step 4: Render the selector**

Add a compact `<select aria-label="App">` with `All apps` and every active app. Submit on change with a small script carrying the existing CSP nonce. Preserve non-app query parameters where meaningful and clear page numbers when scope changes.

- [ ] **Step 5: Add per-app comparison to Overview**

In combined scope, render a table with app, MRR, paying installations, 30-day movement, and churn. Each app name links to the same page with `?app=<slug>`.

- [ ] **Step 6: Verify desktop and mobile navigation**

Use Playwright CLI at 1440x900 and 390x844. Assert the selector is usable, app labels do not overflow, the mobile menu does not cover content, and switching apps changes the headline values without losing navigation.

- [ ] **Step 7: Commit the multi-app UI**

Run:

```bash
uv run pytest tests/test_web.py -q
git add src/app_dashboard/web.py src/app_dashboard/templates/base.html src/app_dashboard/templates/overview.html src/app_dashboard/templates/actions.html src/app_dashboard/templates/churn.html src/app_dashboard/templates/retention.html src/app_dashboard/templates/funnel.html src/app_dashboard/templates/traffic.html tests/test_web.py
git commit -m "feat: add all-app and per-app dashboard scopes"
```

## Task 7: App-Specific Usage, GA4, And Annotations

**Files:**
- Modify: `src/app_dashboard/usage.py`
- Modify: `src/app_dashboard/ga4.py`
- Modify: `src/app_dashboard/annotations.py`
- Modify: `src/app_dashboard/web.py`
- Modify: `tests/test_usage.py`
- Modify: `tests/test_annotations.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Test app-specific integration boundaries**

Assert alpha's usage token cannot write beta events, identical `(shop_gid, event_id)` values coexist across apps, annotations require a selected app, and GA4 upserts for the same date/dimension coexist across apps.

- [ ] **Step 2: Replace the usage route**

Implement `POST /ingest/usage/{app_slug}`. Resolve the app before parsing the body, compare `X-Usage-Token` against that app's token, validate against that app's allowlist, and pass `app_id` to parse/ingest/read queries.

- [ ] **Step 3: Scope usage calculations**

Require `app_id` for `has_usage_data`, `activation_cohorts`, `time_to_activation`, and `at_risk_shops`. Join shops and subscriptions on both app and shop identity.

- [ ] **Step 4: Scope GA4 storage and sync**

Use `upsert_rows(conn, app_id, rows)` and `sync_ga4(conn, app, client)`. Query each configured app's own earliest date and write `(app_id, date, dimension, value)`.

- [ ] **Step 5: Scope annotations**

Require an app for `add` and `remove`. `recent` and `by_month` accept `Scope`; combined results include app name and slug. Only the selected-app UI renders the annotation form.

- [ ] **Step 6: Commit app integrations**

Run:

```bash
uv run pytest tests/test_usage.py tests/test_annotations.py tests/test_web.py -q
git add src/app_dashboard/usage.py src/app_dashboard/ga4.py src/app_dashboard/annotations.py src/app_dashboard/web.py tests/test_usage.py tests/test_annotations.py tests/test_web.py
git commit -m "feat: scope usage traffic and notes per app"
```

## Task 8: Scoped Exports, Health, Digest, And FAQ

**Files:**
- Modify: `src/app_dashboard/export.py`
- Modify: `src/app_dashboard/markdown_export.py`
- Modify: `src/app_dashboard/ops.py`
- Modify: `src/app_dashboard/digest.py`
- Modify: `src/app_dashboard/faq.py`
- Modify: `tests/test_export.py`
- Modify: `tests/test_ops.py`
- Modify: `tests/test_digest.py`

- [ ] **Step 1: Write export and operations isolation tests**

Assert combined exports contain `scope: all` and `app_slug` on owned rows; selected exports contain only one app. Assert stale health names the app/source pair and one app's recovered sync clears only its own alert. Assert digest totals equal per-app sums and include a per-app table.

- [ ] **Step 2: Thread Scope through HTML twins and JSON**

Change `full_export`, `render_page`, and customer Markdown to accept `Scope` and selected app metadata. Preserve `?app=` in frontmatter source URLs and Copy MD fetches. Do not export contact data.

- [ ] **Step 3: Make health app-aware**

Return one status per active app/source with app slug/name, age, error, and first-sync completeness. Combined status is healthy only when every active app has completed both Partner sources.

- [ ] **Step 4: Aggregate digest safely**

Use comparable financial/lifecycle metrics across all apps and append per-app rows. Exclude GA4 and activation from the aggregate digest. Store the digest send marker in a dedicated global operations table or a clearly documented nullable/global sync record; do not overload an arbitrary app's sync state.

- [ ] **Step 5: Update FAQ definitions**

Explain that combined merchants are app installations, why one shop may appear under multiple apps, and why Traffic/activation require a selected app.

- [ ] **Step 6: Commit exports and operations**

Run:

```bash
uv run pytest tests/test_export.py tests/test_ops.py tests/test_digest.py -q
git add src/app_dashboard/export.py src/app_dashboard/markdown_export.py src/app_dashboard/ops.py src/app_dashboard/digest.py src/app_dashboard/faq.py tests/test_export.py tests/test_ops.py tests/test_digest.py
git commit -m "feat: expose multi-app exports and operations"
```

## Task 9: Demo Seed, Invariants, And Documentation

**Files:**
- Modify: `scripts/seed_demo.py`
- Modify: `scripts/check_invariants.py`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docs/configuration.md`
- Modify: `docs/architecture.md`
- Modify: `docs/deploy.md`
- Modify: `docs/usage-events-integration.md`
- Modify: `tests/test_invariants.py`

- [ ] **Step 1: Seed two demo apps with an overlapping shop**

Generate Alpha and Beta under different organizations, with different annual prices. At least one shop must use both apps and have independent subscription histories. Keep all domains `demo-` prefixed and write through the real app-scoped ingest/derive path.

- [ ] **Step 2: Expand the live invariant checker**

Run every existing invariant once per active app and add combined checks:

```text
PASS  All-app MRR equals the sum of per-app MRR
PASS  All-app paying count equals the sum of app installations
PASS  Every owned row has an active app
PASS  Shared shop GIDs remain isolated by app
PASS  Every sync cursor belongs to one app and source
```

Print the app slug on per-app failures and exit non-zero if any check fails.

- [ ] **Step 3: Document the breaking setup**

Replace singular Partner settings with `config/apps.yml` plus token envvars. State clearly that this fork requires a new database and full replay. Document the selector semantics, per-app usage endpoint, non-aggregated Traffic/activation, single scheduler constraint, and verification commands.

- [ ] **Step 4: Run the complete local verification**

Run:

```bash
createdb app_dashboard_multi_demo
DATABASE_URL=postgresql://localhost:5432/app_dashboard_multi_demo uv run python -m app_dashboard.migrate
DATABASE_URL=postgresql://localhost:5432/app_dashboard_multi_demo NO_SCHEDULER=1 uv run python scripts/seed_demo.py --yes
DATABASE_URL=postgresql://localhost:5432/app_dashboard_multi_demo uv run python scripts/check_invariants.py
uv run pytest -q
```

Expected: all invariants and the complete test suite pass.

- [ ] **Step 5: Commit seed and documentation**

```bash
git add scripts/seed_demo.py scripts/check_invariants.py README.md .env.example docs/configuration.md docs/architecture.md docs/deploy.md docs/usage-events-integration.md tests/test_invariants.py
git commit -m "docs: complete multi-app setup and verification"
```

## Task 10: Real Catalog Replay And Browser Acceptance

**Files:**
- Modify: `config/apps.yml` only if live verification finds a configuration correction.
- No production code changes unless a failing regression test is added first.

- [ ] **Step 1: Configure the five organizations and 16 apps**

Use these Partner organization/token mappings:

```text
3508770 -> SHOPIFY_PARTNER_TOKEN_3508770
4626496 -> SHOPIFY_PARTNER_TOKEN_4626496
4653231 -> SHOPIFY_PARTNER_TOKEN_4653231
4742901 -> SHOPIFY_PARTNER_TOKEN_4742901
4821379 -> SHOPIFY_PARTNER_TOKEN_4821379
```

Populate the 16 known Partner App GIDs from Mantle. Use exact annual charge amounts already verified from events: EU Tax `99.90,249.90,499.00`; Happy Birthday `150.00,400.00`; Bol Sync `99.90,290.00`; Image Translate `90.00`; EORI `190.00`; ISBN `100.00`; empty for apps with no verified annual amount.

- [ ] **Step 2: Replay into a new live comparison database**

Run migrations, then invoke the coordinator once for lifecycle events and once for transactions. Do not enable the scheduler until the initial replay completes.

- [ ] **Step 3: Run invariants before serving**

```bash
DATABASE_URL=postgresql://localhost:5432/app_dashboard_multi_live uv run python scripts/check_invariants.py
```

Stop on any failure. Fix the root derivation or scoping rule with a failing test before replaying.

- [ ] **Step 4: Browser-smoke the accepted workflows**

Start Uvicorn and verify with Playwright:

1. `All apps` Overview renders non-zero combined figures and the 16-row comparison.
2. Selecting EU Tax changes title and MRR to EU Tax only.
3. Selecting Bol Sync changes data without carrying EU Tax rows.
4. Customers shows shared shops with app ownership.
5. Traffic and activation require one selected app.
6. Churn, Retention, Actions, Copy MD, and Download JSON preserve scope.
7. Desktop and mobile screenshots have no clipped selector, overlapping text, or blank charts.

- [ ] **Step 5: Final full suite and clean-worktree check**

```bash
uv run pytest -q
git diff --check
git status --short
```

Expected: full suite passes, no whitespace errors, and only intentional files remain.

- [ ] **Step 6: Commit any verified catalog correction**

Only when Step 1 required a correction:

```bash
git add config/apps.yml
git commit -m "config: finalize live multi-app catalog"
```
