# Multi-App Dashboard Design

## Objective

Turn the single-app dashboard into an independently maintained multi-app
dashboard for all 16 apps across five Shopify Partner organizations.

The default view aggregates all apps. An app selector narrows every supported
report to one app. Financial and lifecycle metrics count app installations, so
a shop using two apps contributes once to each app and twice to the combined
total. This makes every combined total equal the sum of its per-app totals.

## Chosen Approach

Build native multi-app ownership into the relational model and pipeline.

Rejected alternatives:

- A database or schema per app duplicates schedulers and makes cross-app
  retention, customer, and export queries needlessly complex.
- Reading Mantle's database directly couples this fork to Mantle's schema and
  derivation rules, defeating the goal of an independent pipeline.

## Configuration And Identity

Add two core entities:

- `organizations`: a Shopify Partner organization, identified by
  `partner_org_id` and the name of its token environment variable.
- `apps`: a dashboard app with a unique slug, Partner App GID, display name,
  annual prices, listing URL, and optional GA4 and usage configuration.

Commit non-secret configuration in `config/apps.yml`. Keep tokens and service
account credentials in environment variables such as
`SHOPIFY_PARTNER_TOKEN_3508770`. The YAML stores environment variable names,
never secret values.

At startup, validate the YAML and reconcile it into `organizations` and `apps`.
Reject duplicate slugs and Partner App GIDs, missing token environment
variables, invalid annual prices, inconsistent usage event settings, and
partially configured GA4 integrations.

Removing an app from YAML must not silently delete its history. Configuration
reconciliation should fail with an actionable message until the app is
explicitly marked inactive or its data is deliberately removed.

## Data Model

Every app-owned table gets a required `app_id`:

- `raw_app_events`
- `app_events`
- `charges`
- `subscriptions`
- `shops`
- `transactions`
- `sync_state`
- `usage_events`
- `ga4_daily`
- `annotations`
- `tracking_events`

All identities and deduplication keys become app-scoped. Representative keys:

- shops: `(app_id, shop_gid)`
- raw events: `(app_id, platform_event_id)` plus the existing feed dedupe rule
- derived events: `(app_id, platform_event_id)`
- charges and subscriptions: `(app_id, gid)`
- transactions: `(app_id, transaction_gid)`
- sync state: `(app_id, source)`
- usage events: `(app_id, shop_gid, event_id)`
- GA4 rows: `(app_id, date, dimension, value)`

The same Shopify shop GID may occur in any number of apps without collision.
Reports treat those rows as separate app installations. Merchant detail pages
may group them for presentation, but must not deduplicate financial metrics.

This is a deliberate breaking change. The fork starts with a new database and
replays Partner events and transactions. It does not maintain a legacy
single-app fallback or attempt to infer app ownership for existing rows.

## Synchronization

Use one scheduler coordinator on one machine:

1. Load active apps.
2. Group apps by Partner organization.
3. Construct one Partner API client per organization.
4. Synchronize apps sequentially within controlled API limits.
5. Record cursor, last success, and failures per `(app_id, source)`.

Every pipeline function takes an explicit app object or `app_id`. Write paths
must treat a missing app scope as a programming error. One app's failure is
logged and recorded without stopping the remaining apps.

Annual-price inference is per app. A price that is annual for one app must not
classify the same amount as annual for another app.

Initial history replay reports progress per app. Combined pages remain
available while a replay runs, but display a prominent incomplete-data warning
until every active app has completed its first event and transaction sync.

## Dashboard Scope

Add a global app selector with `All apps` as the default. Represent a selected
app as `?app=<slug>` and preserve it through navigation, forms, pagination,
Markdown mirrors, and JSON exports.

In `All apps` scope:

- Overview aggregates MRR, collected revenue, installs, churn, and retention.
- A per-app comparison table shows MRR, paying installations, growth, and churn.
- Customers lists one row per app installation with an app column.
- Merchant detail groups one Shopify shop's app installations and shows a
  separate lifecycle and payment history for each app.
- Actions, Churn, and Retention include app labels and support narrowing to one
  app.
- Existing annotations are labeled by app. Creating an annotation requires a
  selected app.

In a selected-app scope, existing page behavior and terminology stay as close
as possible to the current dashboard.

Traffic and activation are not aggregated. GA4 properties, accepted usage
events, and activation meanings are app-specific. In `All apps`, those pages
ask the operator to choose one app. With one app selected, they show the current
GA4 and usage reports.

Every exported row that can be app-owned includes `app_slug`. Export metadata
states whether the export covers all apps or one selected app.

## Usage Ingest

Replace the global usage endpoint with an explicit app route:

`POST /ingest/usage/{app_slug}`

Each app has its own usage token and event allowlist. Reject unknown or inactive
apps, wrong tokens, and disallowed event types before writing anything. Keep
the existing request-size, rate-limit, idempotency, and immutable-event
properties.

## Operations And Error Handling

Health and stale-sync state are app-specific. The combined health strip shows
which app and source is stale or failing. Slack alerts include the app name and
deduplicate per app and incident.

Per-app errors must never advance another app's cursor. A partially failed sync
must retain enough state to retry idempotently.

The weekly digest may aggregate comparable financial and lifecycle metrics, but
must label per-app contributions. GA4 and activation remain excluded from an
aggregate digest unless one app is explicitly selected.

## Verification

Write tests before implementation for configuration, migrations, ingestion,
derivation, statistics, routes, and exports.

Required invariants:

- all-app MRR equals the sum of per-app MRR;
- combined counts equal the sum of app installations;
- no lifecycle or financial row lacks `app_id`;
- the same `shop_gid` can exist in multiple apps without collision;
- cursors and sync failures are isolated per app;
- app filters never leak rows from another app;
- annual-price classification is app-specific;
- exports and merchant detail preserve app ownership;
- existing single-app metric invariants pass for every app independently.

Final verification uses a new Postgres database, a full replay for at least two
apps from different Partner organizations, the live invariant checker, the full
test suite, and browser smoke tests for `All apps` plus both individual apps.

## Non-Goals

- Multiple dashboard tenants or user-specific app permissions.
- Aggregating GA4 or activation across apps.
- Preserving the existing single-app database in place.
- Running more than one scheduler instance.
- Reusing Mantle as a runtime dependency.
