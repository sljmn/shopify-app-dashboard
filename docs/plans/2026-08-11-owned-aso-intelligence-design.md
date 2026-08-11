# Owned ASO Intelligence Design

## Goal

Build the useful parts of Rankbase directly into Mantle without depending on
Rankbase's API or account. Mantle will use read-only Google Analytics 4 access
and public Shopify App Store data to provide keyword analytics, merchant install
attribution, listing history, and keyword research for every configured app.

Shopify Community monitoring is explicitly deferred to a separate design. It
has a different crawler, deduplication, and notification lifecycle and should
not delay trustworthy ASO analytics.

## Principles

- Store source-derived data locally so reports are fast, reproducible, and not
  coupled to live GA4 availability.
- Keep ASO data separate from lifecycle and financial truth. Shopify Partner
  events remain authoritative for installs; GA4 only describes discovery and
  marketing attribution.
- Never turn missing attribution into `Direct`, estimate a position, or silently
  reinterpret a missing GA4 dimension.
- Scope every stored row and sync state to an app.
- Reuse Mantle's existing catalog, scheduler, manual refresh, authentication,
  periods, responsive controls, and definition registry.

## Configuration

Each Shopify App Store listing gets its own GA4 property ID. A single Google
service account with read-only access may be shared across all properties:

```yaml
ga4:
  property_id: "123456789"
  credentials_env: SHARED_GA4_CREDENTIALS_JSON
```

Credentials remain in environment variables and are never written to the
database or logs. Apps without GA4 configuration continue to expose all
Partner-derived reports and public listing intelligence. Their ASO page shows
the exact missing setup instead of an empty report.

## Data Model

Add dedicated ASO tables rather than expanding `ga4_daily` beyond its current
traffic-summary responsibility:

- `aso_keyword_daily`: app, date, keyword, observed position, locale, country,
  device, search type, users, and install clicks.
- `aso_install_sources`: app, Shopify domain and optional shop ID, install date,
  source, subtype, keyword or referrer, locale, country, and device.
- `aso_listing_snapshots`: app, locale, capture time, normalized listing fields,
  screenshot metadata, and a content hash.
- `aso_listing_changes`: app, locale, capture time, field, and normalized before
  and after values. Unchanged snapshots produce no change rows.
- `aso_popular_keywords`: normalized phrase, discovery source, first seen, and
  last seen.

Use `sync_state` with separate sources for ASO keywords, install attribution,
listing snapshots, and keyword research. Natural compound keys make every
write idempotent.

## GA4 Compatibility Discovery

Before importing an app, query its GA4 metadata and verify the exact Shopify
dimensions and metrics available for keywords, positions, shop domains, and
install sources. The importer uses only verified fields. Compatibility status
is persisted per app/source and rendered in the UI.

A property with partial support may still supply traffic and keywords while
merchant attribution remains unavailable. A clear field-level explanation is
shown. This prevents a changing or incomplete event schema from producing
confidently incorrect reports.

## Synchronization

Run a daily ASO job per configured app. Rewrite the latest seven days on every
incremental run because GA4 processes recent data late. Paginate large reports,
respect quota responses, and use bounded retries. Commit each app and source
independently so one failure cannot block the portfolio.

`Fetch fresh data` includes the recent ASO overlap. `Fetch all data again`
rebuilds all history still available within GA4 retention. Both paths use the
same importer and upserts.

Fetch public listings and Shopify autocomplete at most daily with timeouts,
bounded retries, a descriptive user agent, and no attempt to bypass access
controls. When a public representation changes or becomes unavailable, retain
the last valid snapshot and mark the source stale.

## ASO Interface

Add an `ASO` sidebar destination.

With all apps selected, show one portfolio row per app containing configuration
status, organic users, install clicks, click conversion, top keyword, and the
largest ranking movement.

With one app selected, expose tabs for:

- `Overview`: headline search and install-intent metrics plus movement summaries.
- `Keywords`: sortable keyword performance and position history.
- `Install sources`: shop-level discovery source records.
- `Listing changes`: current localized listing and a before/after change timeline.
- `Research`: Shopify autocomplete phrases cross-referenced with the listing and
  observed traffic.

Filters cover the existing 7/30/90-day and custom period controls plus search
type, locale, country, and device. The keywords table shows latest and average
position, previous-period movement, users, install clicks, conversion, and a
transparent Opportunity score.

The Opportunity score is Mantle's own reproducible measure, not a copied
proprietary algorithm. It combines observed install intent with capped ranking
headroom. Its exact formula and inputs appear in the metric definition and are
covered by deterministic tests.

## Merchant Attribution

Customer detail pages show the recorded source, subtype, keyword or referrer,
install date, locale, country, and device. The Customers report adds source and
keyword columns and filters.

When GA4 has no matching event, render `No attribution found`. Do not substitute
`Direct`. Shopify lifecycle events continue to determine whether and when a
merchant installed; attribution is an optional enrichment joined by app and
normalized `.myshopify.com` domain.

## Listing Intelligence

Store localized title, descriptions, pricing, feature text, and screenshot
metadata. Compare normalized snapshots and create changes only for fields that
actually differ. The UI presents field-level before/after values and correlates
changes with keyword movement without claiming causation.

Keyword research records public autocomplete terms, marks terms already present
in the current listing, and marks terms that have appeared in actual GA4 traffic.

## Failure Semantics And Limits

- History before GA4 connection or outside the property's retention window is
  unavailable and must not be synthesized.
- Consent, blockers, and missing events cause measurement loss.
- A successful empty query, an unsupported dimension, and a failed query are
  three distinct states.
- Ranking and attribution are displayed only when the property exposes the
  required source fields.
- Public-source failures keep the last valid snapshot with a stale timestamp.

## Verification

Use fake GA4 metadata and report clients, saved public-listing fixtures, and the
real Postgres test schema. Cover metadata compatibility, pagination, seven-day
rewrites, app isolation, idempotency, timezones, missing dimensions, source
normalization, listing diffs, Opportunity score inputs, and aggregate-to-row
invariants.

Route tests cover authentication, scoping, filters, empty/configuration/error
states, merchant attribution, and CSV export. Browser checks cover the portfolio
and selected-app views, period and facet controls, wide table containment, and
desktop/mobile layouts.

## Delivery

1. GA4 schema, metadata diagnosis, configuration, and sync status.
2. Keyword and install-click ingestion plus ASO reports and CSV export.
3. Merchant attribution on Customers and customer details.
4. Listing snapshots, change history, and autocomplete research.
5. Production property access, full backfill, and per-app coverage audit.

Reference behavior and available concepts were evaluated against the public
[Rankbase product documentation](https://rankbase.io/documentation) and
[Rankbase API documentation](https://rankbase.io/docs), but the implementation
uses neither Rankbase's API nor its account system.
