# Period report

## Goal

Add a dedicated report that explains what happened across the app portfolio in a chosen period. It must keep subscription value and collected cash separate, use the dashboard's existing metric definitions, and make each app's contribution comparable at a glance.

## Placement and interaction

Add `Period` directly below Overview in the main navigation. The default window is the last 30 days. Available presets are the last 7, 30, or 90 days, this month, and last month. A custom mode accepts an inclusive start and end date.

The report shows the resolved start and end boundaries and compares its totals with the immediately preceding window of equal duration. Presets ending today run up to the current instant. Calendar-month and custom-date boundaries use `Europe/Amsterdam`; database queries receive the equivalent UTC instants. Custom windows may span at most two years.

The existing app selector continues to scope the page. With `All apps` selected, the report compares every app. Selecting a table row opens the same period scoped to that app.

## Summary

The header contains these period totals:

- installs;
- uninstalls;
- net install growth;
- MRR gained;
- MRR lost;
- net MRR movement;
- net collected revenue.

Each total includes its change against the preceding equal-length period. Positive and negative presentation follows the meaning of the metric rather than treating every increase as good.

## Per-app table

The sortable table contains one row per app and these columns:

| App | Installs | Uninstalls | Net installs | MRR gained | MRR lost | Net MRR | Collected |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

MRR gained combines new subscriptions, reactivations, and expansions. MRR lost combines contractions and churn. Net MRR is their signed sum. Collected is cash that reached the payout, not projected subscription value.

## Data definitions

Lifecycle counts come from `app_events`: installs include `installed` and `reinstalled`; uninstalls include `uninstalled`. MRR is reconstructed from corrected `subscriptions` rather than historical `app_events.net_change`. Free subscriptions and active trials are excluded, and configured annual prices are represented at one twelfth of the annual amount. Collected revenue is the sum of `transactions.net_amount`, so refunds, credits, fees, and adjustments are reflected in the recorded cash result.

The implementation groups lifecycle, subscription, and transaction work by app. It must not issue one database query per app. Portfolio totals are derived from the same per-app rows, preventing the summary and table from drifting apart.

## Empty and invalid states

A period wholly in the future says `This period has not started yet`. A valid past period with no matching data gets a normal empty-state explanation. Invalid, reversed, or overlong custom dates render an inline validation message and never produce a framework 422 response.

## Verification

Tests cover preset and custom boundaries, the equal preceding comparison, Amsterdam daylight-saving transitions, app scoping, annual prices, active trials, refunds and adjustments, future and empty periods, invalid dates, and the invariant that per-app rows sum to the displayed portfolio totals. Browser checks cover the desktop table, mobile horizontal scrolling, custom date controls, preset switching, row drill-down, and retained period parameters when changing apps.
