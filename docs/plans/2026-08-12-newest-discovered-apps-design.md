# Newest Discovered Apps Design

## Goal

Make the exact apps behind the Discover growth count immediately visible in newest-first order, including the time Mantle first observed each app.

## Design

Keep the existing `discovery_report` query as the single source of truth. It already excludes the initial baseline and orders apps by `first_seen_at desc, handle`; no new persistence or duplicate report is needed.

Move the existing filters, discovered-app table, and pager directly below the weekly chart. Introduce the section as **Newest apps found**, explain that the timestamp is Mantle's first observation rather than Shopify's publication time, and render it in the dashboard's Europe/Amsterdam timezone. Keep Growth signals below this chronological section because it is an analysis of review and rank movement, not a complete list of newly found apps.

## Verification

Add a web regression test proving the chronological section appears before Growth signals, contains a CET/CEST timestamp, and orders newer apps before older apps. Run the complete test suite and verify the deployed page responds successfully.
