# Merchant Search And Customer Table Design

## Objective

Make a merchant reachable from Overview without first navigating to Customers,
and turn Customers into the operational list for comparing current merchant
state across the app portfolio.

Shopify Plus is intentionally excluded. The Partner API does not expose a
merchant's Shopify plan and this dashboard does not yet import that field from
the individual app databases.

## Overview Search

Place a compact merchant search directly below the sync status and above the
headline statistics. It searches the same app name, shop name, and Shopify
domain fields as the Customers page and respects the active app scope.

HTMX requests a dedicated read-only result fragment after a short typing delay.
Blank input renders no result panel. A non-blank query returns at most eight
matches with merchant, domain, app, installation state, and a direct link to
the scoped merchant detail page. A final link opens the complete Customers
result set when more matches may exist.

The result panel is positioned in normal document flow rather than as an
overlay, so it remains usable at narrow mobile widths and cannot cover the
statistics underneath it.

## Customers Table

Keep the existing additive filters and pagination, but replace the identity-only
table with columns for Merchant, App, Plan, MRR, Status, Installed, and Latest
event. The merchant cell retains the Shopify domain and links to merchant detail.

The values come from current derived state:

- Plan is Trial, Free, Monthly, Annual, or no active plan. Commercial plan names
  such as Starter are unavailable in the Partner feed and are not inferred.
- MRR is the normalized monthly amount of a live paid subscription. Trials,
  free plans, churned subscriptions, and uninstalled shops contribute zero.
- Status distinguishes Trial, Paying, Free, Installed, Cancelled, and
  Uninstalled using the live installation, subscription, and trial records.
- Installed is the recorded first installation timestamp.
- Latest event is the newest derived lifecycle event for that app installation.

The query remains paginated in SQL and enriches each row with lateral aggregate
lookups, avoiding per-row database calls. All filters and the app scope continue
to apply before pagination.

## Responsive Behaviour

The Overview result list stacks its metadata below the merchant name on mobile.
The Customers table keeps its stable column widths and uses the existing
horizontal table scroll on narrow screens; filters wrap without overflowing.

## Verification

Focused tests cover search matching, app scoping, the eight-result limit,
detail links, current plan/MRR/status derivation, latest-event selection, and
filter preservation. The full suite must pass. Browser checks at desktop and
mobile widths verify the live search flow, table readability, and absence of
page-level horizontal overflow.
