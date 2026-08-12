# App Store Discovery Design

## Goal

Index every public Shopify App Store app, discover newly appearing apps, and show how many apps are first observed per week across every category.

## Definition of new

An app is new when its handle appears in Mantle's daily App Store sitemap import for the first time. The initial complete import is a baseline and its apps do not count as new. This is an observation date, not a claimed Shopify publication date.

## Collection

- Fetch `sitemap_apps_en.xml` daily. Parse canonical app handles and listing `lastmod` values, then upsert them in batches.
- Fetch `sitemap_categories_en.xml` twice weekly. Crawl every leaf category's paginated `/all` pages, deduplicate handles, and record category membership and the display name exposed on the card.
- Treat category crawling as enrichment. A partial or failed crawl never deletes previous app or category data.
- Bound requests with timeouts, retry transient responses, cap pagination, and pause between category requests.
- Do not fetch every individual listing page. The sitemap plus category cards supplies the requested discovery data at a much lower request volume.

## Data model

`discovered_apps` stores the canonical handle, optional display name, sitemap modification date, first and last observation times, and whether the row belongs to the initial baseline. `discovery_categories` stores canonical category slugs and their current observed app count. `discovered_app_categories` is a many-to-many relation, because one app can occur in several categories. `discovery_state` stores the baseline completion and latest successful sitemap/category runs.

The weekly chart groups non-baseline `first_seen_at` values into Monday-to-Sunday weeks in Europe/Amsterdam. An app contributes once globally regardless of how many categories contain it. A category-filtered chart still contributes the app once within that category.

## Interface

Add an app-independent `Discover` destination to the sidebar. The page contains:

- indexed-app total, new this week, new in the last 7 days, and last successful scan;
- a compact 12-week bar series whose bars and labels expose exact counts;
- search and category filters;
- a newest-first table with app name, handle, categories, first observed date, listing update date, and a direct Shopify App Store link;
- an honest baseline/observation note near the metrics.

The interface follows the existing quiet, dense dashboard language. On mobile, metric summaries wrap, filters stack, the weekly series remains horizontally legible, and the result table uses the app's existing horizontal table overflow behavior.

## Operations and failure behavior

The daily sitemap job runs independently from Partner API and ASO jobs. Category enrichment runs twice weekly and starts after boot with a delay to avoid competing with lifecycle synchronization. Empty responses, malformed sources, and HTTP failures are logged and leave the previous successful dataset intact.

## Verification

Tests cover sitemap/category parsing, first-observation preservation, initial-baseline exclusion, duplicate handles, category membership, week boundaries, partial failures, scheduler registration, authenticated routing, filtering, and mobile rendering. A real-source smoke check verifies Shopify's current sitemap/page shapes without making the test suite depend on the network.
