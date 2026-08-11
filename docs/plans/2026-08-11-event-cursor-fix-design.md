# Event Cursor Fix Design

## Problem

The Partner `app.events` connection is newest-first. The dashboard persisted
the cursor at the end of one poll and used it as the starting cursor of the next
poll. That resumes toward older pages and can never see events added above the
saved cursor. EU Tax Exemption Easy therefore missed Aeris' August 11 install
and subscription even though the older Mantle implementation ingested both.

## Design

Match the proven Mantle polling model:

- every poll starts pagination with `after = null`;
- incremental polls send `occurredAtMin = last_synced_at - poll overlap`;
- page cursors exist only inside that poll;
- full-history polls omit `occurredAtMin`;
- successful polls persist `last_synced_at` and clear the obsolete cursor.

The raw-event unique keys and per-shop derivation remain unchanged, so overlap
and full replay are idempotent. Existing non-null cursors require no migration:
the corrected reader ignores them and the next successful poll clears them.

## Recovery and verification

After deployment, run a full lifecycle replay for every active app to recover
events already skipped by the old cursor. Verify that Aeris has August 11
`installed` and `subscribed` events, that the subscription contributes $24.99
MRR, and that all live database invariants still pass.
