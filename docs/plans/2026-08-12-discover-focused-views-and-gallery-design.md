# Focused Discover Views And Listing Gallery

## Goal

Make true Shopify App Store launches and listing changes independently useful,
and let users inspect every current or archived listing screenshot at full size.

## Navigation

Discover gets local subnavigation with **Overview**, **New launches**, and
**Listing updates**. These stay under the existing Discover sidebar item.
Overview keeps catalog search, metrics, weekly activity, growth signals, and
category opportunities. The two focused views each get their own route, filters,
table, pagination, and empty state.

## New launches

`/discover/new` only reads `discovery_events.event_type = 'discovered'` for apps
whose `is_baseline` is false. Sitemap `lastmod`, listing changes, relistings, and
the initial catalog import never qualify as launches. Rows are newest first and
show observed time, app, category, BFS, pricing, developer, reviews, research,
and the public listing. Filters cover search, category, BFS, pricing model, and
7/30/90 day or all-time periods.

## Listing updates

`/discover/updates` reads listing-update events. Each row shows Shopify's
previous/new `lastmod` and the latest verified snapshot diff when one exists.
Changed-field badges identify title, subtitle, description, features, pricing,
screenshots, and other captured changes. A verified update links directly to
the existing before/after Compare view; unverified sitemap signals are labeled
clearly instead of implying Mantle knows what changed.

## Screenshot gallery

Every `.listing-gallery` image in Overview and Compare is a native button that
opens one page-level dialog. The dialog shows the original archived image with
contain sizing, a caption and position counter, previous/next controls, and a
close button. It supports Escape, arrow keys, backdrop click, focus restoration,
and body scroll locking. Mobile uses the same controls with full-width media;
no new JavaScript dependency is added.

## Verification

Query tests prove baseline and update events cannot leak into New launches and
that verified update rows expose changed fields and comparison ids. Route tests
cover both focused pages and preserved filters. Browser checks cover desktop and
390px mobile layouts, keyboard operation, opening each gallery, navigation,
closing, and nonblank full-size media.
