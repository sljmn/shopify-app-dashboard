# ASO Rank Tracker Design

## Goal

Track manually selected Shopify App Store keywords, preserve the daily top 100 results, and show app and keyword movement over time.

## Product Boundary

The tracker is a separate **Rank Tracker** view under ASO. Existing GA4 keyword reports remain traffic analytics; rank tracking is active measurement of public Shopify search results. The two datasets must not be merged.

## Search Scope

Each keyword belongs to a named list and has a locale plus a market label. Shopify's public search supports locale directly. Mantle records country as a requested market dimension, but does not claim true geolocated country rankings until a country-specific proxy is configured. This limitation is visible in the interface.

## Collection

Shopify search results are loaded from the `search_page` Turbo frame. A page currently contains 24 organic app results. The collector requests five pages, removes duplicate handles while preserving order, and stores only positions 1 through 100. It ignores guide/story links and validates every Shopify app handle.

Each successful scan is atomic: a scan row and all result rows commit together. An empty or partial first page fails without replacing the prior successful snapshot. Retries follow the existing public App Store retry policy. A daily scheduler scans active keywords; **Scan now** scans one keyword on demand.

## Data Model

- `aso_rank_lists`: name, status, timestamps.
- `aso_rank_keywords`: list, keyword, locale, country, active state, last scan metadata; unique per list/keyword/locale/country.
- `aso_rank_scans`: keyword, captured time, result count, status/error.
- `aso_rank_results`: scan, discovered app, position, displayed name, review count, rating, BFS flag; unique per scan and position/app.

Apps are upserted into the existing `discovered_apps` catalog. There is no second app identity table.

## Interface

- Portfolio view: lists, keyword count, last scan, failures, and biggest movement.
- List detail: add keywords with locale/market, scan status, current top app, tracked-app matches, and actions.
- Keyword detail: current top 100; movement versus yesterday, 7 days, and 30 days; badges for new, dropped, and returned results; direct listing and internal app links.
- App detail: all tracked keywords where the app currently ranks plus position history.

Tables remain horizontally scrollable on mobile, while keyword/list controls stack into a single column. Rank movement uses positive values for improvement and explicitly labels missing historical baselines.

## Verification

Parser fixtures cover Turbo-frame markup, pagination, promoted non-app links, deduplication, and the 100-result cap. Database tests cover atomic scans and comparisons. Web tests cover authenticated CRUD, manual scan, filters, empty states, and mobile-safe markup. Scheduler tests prove daily idempotency.
