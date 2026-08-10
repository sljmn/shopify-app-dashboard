# Active Trials Design

## Objective

Make the dashboard's paying metrics agree with Mantle's definitions and expose
current Shopify trials as a separate, actionable portfolio.

## Source Of Truth

The app-event feed is not sufficient for current trial state. Its inline
`AppSubscription` contains the charge amount and `billingOn`, but not
`trialEndsAt` or `cancelAtEndOfCycle`. The Partner API's
`activeSubscription(appId, shopId)` query is the source of truth for those
fields and is the same boundary Mantle uses.

Store one current snapshot per `(app_id, shop_gid)`. A `nil` API response deletes
the snapshot because the shop no longer has an active subscription. The row
contains the current legacy subscription ID, billing period, trial end,
cancel-at-cycle flag, plan handle/description, currency, raw payload, and
observation time.

## Synchronization

Add an independent active-subscription sync. It iterates currently installed
shops per app, queries Shopify sequentially with the existing API throttle, and
upserts or removes each snapshot. Its sync-state source is separate from events
and transactions so one failure cannot advance another cursor.

Run the full refresh every six hours. The first local backfill can also be run
explicitly. A full refresh is intentionally separate from the 15-minute event
poll: hundreds of per-shop queries must not delay lifecycle alerts.

## Metric Semantics

A paying installation must satisfy all of these conditions:

- the app is still installed;
- the derived subscription is active;
- normalized monthly MRR is greater than `$0.01`;
- the current active-subscription snapshot is not in trial.

Free plans and current trials are excluded from MRR, paying-customer count,
ARPU, plan mix, and other paying cohorts. Scheduled cancellation does not remove
a paid subscription before its billing period ends. A trial scheduled to cancel
still appears on Trials, but its value is excluded from "MRR at stake".

## Trials Experience

Add a first-class `Trials` page to the sidebar. It respects the global app
selector and shows:

- running trial count;
- trials ending in the next seven days;
- total trial value per month;
- converting MRR at stake;
- MRR already scheduled to cancel;
- one row per merchant with end date, app, merchant detail, storefront, plan,
  monthly value, and conversion/cancellation status.

The customer detail page identifies a live trial and its end date. Rename the
existing Actions "Trial watch" copy to "New installs without a subscription";
that list remains useful but is not presented as actual Shopify trial data.

## Failure And Empty States

Before the first active-subscription refresh, paying metrics can only exclude
free plans. The Trials page states that no current trial snapshots have arrived
rather than claiming there are zero trials. Sync health records a distinct
source so staleness can be diagnosed without marking lifecycle data stale.

## Verification

- Free subscriptions never count as paying or MRR.
- Current trials never count as paying or MRR.
- Paid non-trial subscriptions still count.
- Trial rows are isolated by app scope and link to the correct merchant.
- Cancel-scheduled trials count in total trial value but not MRR at stake.
- A `nil` active-subscription response removes the prior snapshot.
- One app's API failure does not stop the remaining apps.
- Live backfill, invariant checks, the full test suite, and desktop browser smoke
  tests pass.

