# Activity and portfolio design

## Decision

The multi-app dashboard remains the single product. The Rails Mantle app is a
read-only reference while useful reports are rebuilt against the new
dashboard's scoped data model.

This increment adds the two reports that are already supported by trustworthy
Partner API data: a complete activity feed and stronger per-app portfolio
statistics. It also removes Markdown mirrors, the JSON download, and Tips.

## Activity

Add `/activity` as a first-class, app-scoped page. It reads the derived
`app_events` table, which is already the source for the lifecycle and MRR
reports. The page shows 100 events at a time in reverse chronological order.

The existing app selector scopes the feed. A date and event-type filter narrow
it further. Every row shows the event, merchant, app, timestamp, and MRR delta.
Merchant names link to the dashboard detail page; storefront domains link to
the shop itself. Pagination preserves all active filters.

The short latest-activity table remains on Overview and links to the complete
feed.

## Portfolio statistics

The all-app Overview table becomes the portfolio decision table. For each app
it shows current MRR, installed shops, paying shops, paid share, monthly
subscription churn, LTV, current trials, and potential trial MRR.

MRR and paying shops continue to exclude free plans and active trials. LTV and
monthly subscription churn use the existing 90-day unit-economics calculation.
Current trials come from the live active-subscription snapshot.

Historical trial conversion is intentionally absent. The current schema stores
the latest active subscription snapshot, not a complete history of trial
outcomes, so it cannot yet calculate an honest conversion percentage.

## Removed product surface

Remove the Markdown page mirrors, customer Markdown route, JSON export, FAQ,
Copy MD control, Download JSON control, and Tips panel. Delete their code,
templates, documentation, and dedicated tests. Existing metric definitions and
inline explanations remain because they help a reader interpret the numbers in
the dashboard itself.

## Verification

Direct tests cover activity filtering, scoping, ordering, pagination, MRR
deltas, portfolio calculations, and the removed routes. The full test suite
must pass. A browser check at desktop and mobile sizes verifies navigation,
table overflow, filter layout, and the absence of the removed controls.
