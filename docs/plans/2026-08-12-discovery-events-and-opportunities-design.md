# Discovery Events And Opportunities Design

## Goal

Separate newly discovered App Store apps from listing updates and removals, then use those trustworthy states as the basis for competitor diffs, pricing/developer enrichment, alerts, dormant detection, and category opportunity reporting.

## Data Model

`discovered_apps.first_seen_at` remains the first time Mantle observed a handle. `listing_updated_on` remains Shopify's current sitemap `lastmod` and is never presented as a launch date.

Add state to `discovered_apps` for consecutive missing sitemap scans and confirmed delisting. Add an immutable `discovery_app_events` ledger with `discovered`, `listing_updated`, `delisted`, and `relisted` events. A listing update is emitted only when an existing non-null sitemap `lastmod` changes. An app becomes delisted after three consecutive successful complete sitemap scans omit it; seeing it again creates a relisted event.

The existing `discovery_listing_snapshots` and `discovery_listing_changes` remain the authoritative content-diff system. Sitemap `listing_updated` is a signal; a snapshot diff is verified evidence of what changed.

## Enrichment And Reporting

Every newly discovered app is added to the existing watchlist pipeline so its public listing is captured. The newest-app table can then show developer, developer portfolio size, pricing type, price points, trial text, reviews, and explicit dates for discovery, Shopify `lastmod`, and the last verified content change.

Discover shows separate seven-day counts and an event-type filter. The chart starts at the first recorded event rather than rendering empty pre-baseline weeks. Growth signals honor the selected category. Empty growth states state when the next Tuesday/Friday category scan is expected.

Category saturation uses current category membership and the latest available observations/snapshots. It reports app count, review distribution, zero-review share, pricing coverage, paid share, and average observed monthly price. Any score is labelled as a heuristic and exposes its coverage rather than presenting incomplete pricing as complete market data.

## Alerts

Users can follow apps as today and additionally follow categories. New category members and verified listing changes create durable alert records. Slack delivery uses the existing webhook integration and records successful delivery, making retries idempotent. When no webhook is configured the alert remains visible in Mantle and can be delivered after configuration.

## Safety And Verification

Empty or failed sitemap fetches never mutate presence state. State transitions are idempotent. Tests cover new/update/delist/relist classification, the three-scan threshold, report filters, enrichment, category statistics, and alert deduplication. Desktop and mobile Discover/Watchlist pages are checked with Playwright before deployment.
