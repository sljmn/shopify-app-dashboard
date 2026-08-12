# Competitor Watchlist And Listing History Design

## Goal

Let operators search every indexed Shopify App Store listing, follow promising apps manually or automatically, and understand how their public listing, reviews, and category positions develop over time.

## Scope

The feature extends the app-independent Discover area. It does not attach competitor data to an owned Partner app and does not change the existing owned-app ASO tables. The first production increment includes universal search, following, daily full-listing snapshots, archived media, an app detail view, version diffs, automatic follows, and an in-dashboard weekly summary.

Cross-comparing competitor listings with an owned listing is a later increment after enough competitor history exists. The stored snapshots and normalized changes are designed to support that without a migration.

## Approach

Use a dedicated public-app watchlist keyed by `discovered_apps.id`. Reuse the proven Shopify listing parser, retry boundaries, stable hashing, and change extraction patterns from `listing_intelligence.py`, but persist competitor snapshots separately from owned `apps.id` snapshots. This avoids a risky migration of the existing ASO history and keeps the meaning of each foreign key explicit.

Do not fully scrape all indexed listings. The sitemap and category crawl continue to cover the whole store cheaply. Only followed listings receive a daily full scrape and media archive.

## Following

An indexed app can be followed in two ways:

- **Manual:** an operator selects Follow from search results or the app detail page.
- **Automatic:** the category discovery job follows apps that qualify as Rising gems or New contenders.

One watchlist row exists per discovered app. It records the original follow source, follow time, active state, last successful scan, last attempted scan, and sanitized failure status. An automatic follow is not automatically removed when the app later loses its signal. An operator can unfollow it explicitly. Re-following reactivates the same record and preserves all history.

## Universal Search

The existing Discover filter only queries non-baseline apps. Add a separate universal search over all `discovered_apps`, matching display name, handle, category, and developer when available. Results expose current review count, rating, best rank, follow state, and the direct App Store URL.

## Listing Snapshots

The daily watchlist collector fetches `https://apps.shopify.com/<handle>` for each active followed app. It captures, when publicly present:

- name, subtitle or short positioning, description, and feature bullets;
- pricing plans, free plan and trial text;
- icon, screenshots, and video references;
- rating and review count;
- developer name and public developer details;
- supported languages, integrations, and other public metadata.

The canonical JSON payload is content-hashed. An unchanged listing updates scan health but creates no duplicate snapshot. A changed listing creates an immutable snapshot and normalized change rows for every changed field.

## Media Archive

Icons and screenshots are downloaded only for changed snapshots. Media bytes are deduplicated by SHA-256 and stored outside Postgres in a configured archive directory or object-store-compatible mounted path; Postgres stores the digest, MIME type, dimensions, size, and relative object key. A snapshot references media by digest, so old versions remain renderable when Shopify replaces or removes the source URL.

A snapshot becomes current only after its referenced media has been stored successfully. A partial media failure leaves the previous valid version untouched and records a sanitized scan failure.

## App Detail

Each indexed app gets a canonical `/discover/apps/<handle>` detail page with four views:

1. **Overview:** follow state, current listing facts, categories, reviews, rating, ranks, and latest meaningful change.
2. **Growth:** review and same-category rank history from discovery observations.
3. **Listing history:** immutable listing versions, changed fields, and archived visual assets.
4. **Compare:** select two versions and inspect text, pricing, metadata, and screenshot differences.

On mobile, search results are compact rows and comparisons stack before/after vertically. Wide metric histories use contained horizontal scrolling rather than compressing labels.

## Intelligence Signals

Every listing version yields explainable change signals:

- added and removed title, description, and feature terms;
- benefits that became more or less prominent;
- plan, price, free-plan, or trial changes;
- screenshot count, ordering, additions, removals, and replacements;
- optimization frequency;
- review velocity and category-rank movement before and after a change.

Before/after growth is presented as correlation, never as proven causation. No competitor metric claims installs, revenue, or Shopify publication date because those values are not public.

## Weekly Summary

Mantle adds an authenticated weekly Watchlist summary containing:

- apps followed during the week and their follow reasons;
- largest review gains and category-rank improvements;
- meaningful listing changes grouped by app;
- recurring pricing, positioning, and media patterns.

The initial delivery is an in-dashboard summary. Its query boundary can later feed Slack or email without changing storage.

## Scheduling And Failure Handling

- The full-store sitemap remains daily.
- The category crawl remains Tuesday and Friday and evaluates automatic follows after a successful complete crawl.
- Active followed listings are scraped daily with bounded concurrency, retry/backoff, and per-app failure isolation.
- Empty or structurally invalid listing responses never create a version.
- Errors store only stable codes such as `HTTPStatusError`, `listing-json-ld-missing`, or `media-download-failed`; tokens, response bodies, and URLs containing secrets are never persisted.
- All persistence operations are idempotent. A retried scan with identical content creates no duplicate snapshot or media object.

## Verification

Automated coverage must prove:

- manual follow, unfollow, and re-follow behavior;
- automatic follow without duplicate rows;
- unchanged content produces no new version;
- text, price, metadata, and media changes produce exact diffs;
- failed or partial scrapes preserve the last valid snapshot;
- archived media remains addressable after source URLs change;
- baseline apps are not treated as newly launched;
- weekly summaries contain only changes inside their period;
- request limits, retries, and per-app isolation;
- authenticated routes, CSRF on writes, path traversal prevention for archived media;
- responsive desktop and mobile layouts with no overlap.

