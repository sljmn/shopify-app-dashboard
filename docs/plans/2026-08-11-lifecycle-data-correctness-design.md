# Lifecycle Data Correctness

## Goal

Make the Python dashboard's lifecycle ledger agree with Shopify's raw events and current active-subscription snapshots, especially across plan changes, freezes, unfreezes, and abandoned charges.

## Scope

This increment fixes lifecycle and MRR correctness. Historical trial outcomes and product/GA4 instrumentation remain separate projects because they require new upstream data rather than a correction to the existing Partner API feed.

## Chosen Architecture

Keep the two existing representations, but give each one a precise job:

- `app_events` is the canonical lifecycle movement ledger. Its signed `net_change` values independently reconstruct current MRR and will become the historical source after trial outcomes are persisted.
- `subscriptions` is the current materialized subscription state used for fast current-MRR and merchant-detail queries.
- `active_subscriptions` is Shopify's current source snapshot. It validates derived state and, when Shopify retains a different legacy subscription ID, reconciles the current interval and price using the latest positive subscription sale on that snapshot ID.

Full replay remains the repair mechanism. A replay updates all derived fields for a stable raw event and removes clean events that the corrected classifier now suppresses. Raw events remain immutable.

## Lifecycle State Machine

The replay carries the current paid subscription, normalized monthly amount, and whether the shop has ever paid.

- A first paid activation emits `subscribed`.
- An activation after a genuine zero-MRR period emits `resubscribed`.
- A different subscription while paying closes the old subscription at the replacement's activation time and emits `upgraded` or `downgraded` with only the net MRR delta.
- A cancellation paired with a replacement activation is suppressed. Pairing follows the Mantle rules: activation within 60 seconds after the cancel, or the same charge activating within five seconds before it.
- A real cancellation emits `unsubscribed` and removes its normalized monthly amount.
- Freeze emits `subscription_frozen` and removes MRR. Unfreeze emits `subscription_unfrozen`, restores MRR, and reopens the subscription's current state.
- Declined and expired charges emit `charge_abandoned` with zero MRR. Expired events are backdated by 60 hours, clamped to the shop's first lifecycle event.
- Uninstall continues to end every live subscription. Shopify-driven deactivation remains separately identifiable through the raw event type.

## Replay And Idempotency

`platform_event_id` remains the stable derived-event key. Conflict handling refreshes every deterministic derived field rather than preserving obsolete classifications. After each installation replay, clean rows sourced from that installation but not emitted by the new classifier are deleted; this removes old plan-change cancellations without manufacturing new platform ids.

Subscription rows are rebuilt deterministically for the installation. Reusing a subscription id after unfreeze clears its current `churned_at`; historical gaps remain represented by the clean event ledger.

## Reporting And Validation

Historical MRR stays subscription-state based in this increment because the replaceable current trial snapshot cannot exclude past trial windows honestly. Current MRR continues to use live `subscriptions`, while the cumulative clean-event ledger provides an independent current-state total. The live invariant command adds:

- current state MRR versus cumulative event-ledger MRR;
- installed Shopify `active_subscriptions` versus derived live subscriptions;
- raw supported lifecycle types versus emitted clean events;
- no unsuppressed plan-change cancellation within the defined correlation windows.

Churn and LTV use real clean churn/resubscription events so a subscription id replaced during a plan change no longer counts as customer loss.

## Failure Handling

An event without a usable charge is logged and skipped without aborting other installations. Unknown raw types remain stored and logged. Replay is per installation, so one malformed history does not block other shops.

## Verification

Focused tests cover both cancel orderings, genuine cancellation, upgrade and downgrade deltas, resubscription, freeze/unfreeze, declined/expired abandonment, replay cleanup, annual normalization, and snapshot divergence. The complete test suite and live invariant command must pass before a full-history replay. After replay, current MRR, event counts, likely plan-change cancellations, and snapshot mismatches are compared with the captured baseline.
