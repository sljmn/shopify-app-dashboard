# Discover category dashboard

## Problem

Selecting a Shopify App Store category currently keeps Discover in event mode. A URL such as
`/discover?activity=new&category=store-management-security-anti-theft` therefore shows no rows when
the category contains only baseline apps, even though Mantle has current category observations for
92 apps. The empty event list hides the useful data the user intended to inspect.

## Product behavior

When a category is selected, Discover becomes a category dashboard. It shows the category name,
coverage metrics and every currently observed app in that category. The default ordering follows
the latest category position. Recent activity is a filter over the category inventory instead of
the page's primary data source.

The category dashboard supports:

- app name or handle search;
- all apps, newly discovered, gaining reviews, fastest review growth, listing changed and delisted
  views;
- Built for Shopify status;
- current category rank, review count, 7- and 30-day review movement, rating, pricing, developer and
  a direct Shopify listing link.

Apps remain visible when review history, pricing or a listing snapshot is unavailable. Unknown
values render as an explicit dash and never remove a row.

Without a category filter, the current global Discover page and event-oriented activity tabs stay
unchanged.

## Data and queries

Add a dedicated category report query built from `discovered_app_categories`,
`discovery_categories`, `discovered_apps` and the latest `discovery_app_observations`. Lateral
queries obtain comparison observations for 7 and 30 days ago, the latest listing snapshot and the
latest verified listing change. Existing pricing parsing and BFS fields are reused.

The report returns summary coverage, filtered rows and pagination. Filtering happens in SQL so a
category with hundreds of apps remains bounded. Activity filters use existing discovery events and
listing changes; no new persistence or scraper is introduced.

## Interface

The web route detects a selected category and renders a category-specific section in the existing
Discover template. Summary cards identify the current inventory and measurement coverage. A compact
filter toolbar controls signal, BFS status and search. The responsive table follows the existing
catalog card pattern on mobile.

The global charts, portfolio-wide metrics, activity event table, growth signals and category
opportunity table are hidden in category mode because they answer portfolio questions rather than
the selected category question. A clear action returns to global Discover.

## Reliability and testing

An unknown category returns a clear empty state rather than silently displaying global data.
Database tests cover baseline inventory, review deltas, signals, BFS filters, unknown values,
ordering and pagination. Web tests cover category-mode switching and direct listing links.
Playwright checks the populated and empty states on desktop and mobile.
