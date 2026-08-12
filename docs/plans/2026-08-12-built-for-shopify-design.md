# Built for Shopify visibility

## Goal

Show whether a public Shopify App Store app has earned Built for Shopify (BFS),
both in the Discover catalog and on the public app detail page. Never present an
unverified app as non-BFS.

## Source and status model

Shopify renders a stable `.built-for-shopify-badge` on BFS app cards and listing
pages. Mantle will read that marker from category scans and full listing scans.
The current app status is tri-state:

- `true`: Shopify displayed the BFS badge during the latest check.
- `false`: Shopify returned the app card or listing without the badge.
- `null`: Mantle has not yet checked a source that exposes BFS status.

Each status records when it was checked. A full listing scan is authoritative
and category scans keep the wider catalog current without individually fetching
all 24,000 listings.

## History

`built_for_shopify` becomes part of the immutable listing snapshot. A change in
BFS status therefore creates a new listing version and a field-level diff, just
like pricing or screenshots. The current catalog column is updated at the same
time.

## Interface

Discover gains:

- a `Built for Shopify` column with BFS, Not BFS, or Not checked;
- a filter for All, BFS, Not BFS, and Not checked;
- BFS state in both activity and catalog results where applicable.

The public app detail heading shows a compact BFS badge when earned. The
overview shows the explicit current state and last checked time, including the
unknown state when no qualifying scan has run.

## Failure behavior

Failed or partial scans do not overwrite the previous BFS state. Only a
successfully parsed category card or listing page can update it. Delisting is
kept separate from BFS state.

## Verification

Parser fixtures cover BFS and non-BFS pages. Database/report tests cover all
three states and filtering. Web tests cover the Discover column/filter and the
detail badge. The full existing suite remains green.
