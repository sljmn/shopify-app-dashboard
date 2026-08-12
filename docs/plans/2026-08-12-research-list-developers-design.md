# Research List Developers Design

## Goal

Allow a research list to contain Shopify developers as well as individual apps. Adding a developer follows its complete current portfolio and automatically includes apps discovered by later developer scans.

## Data Model

Add `research_list_developers` with `(research_list_id, discovered_developer_id)` as its primary key and an `added_at` timestamp. The developer remains a first-class list member; its apps are not copied into `research_list_apps`. This prevents stale duplication and lets a refreshed developer catalog appear immediately in every containing list.

## Behavior

- Search developers by name or Shopify partner URL from the list page.
- Adding a developer triggers a catalog scan when it has never been scanned.
- Every app linked to the developer is followed through the existing discovery watchlist.
- Future catalog scans also follow newly discovered apps while the developer belongs to at least one active research list.
- Removing the developer removes only the list membership. Existing app history and watchlist history remain intact, matching app removal behavior.
- List totals show separate Apps and Partners counts.

## Interface

The list page has separate **Apps** and **Partners** work sections. Both use searchable typeahead selectors, tables with direct links, tracking state, and a remove action. The header adds **Add partner** next to **Add app**. Partner rows link to the internal developer dossier and the Shopify developer page.

## Error Handling

Unknown list or developer IDs return the existing 404 behavior. Duplicate membership is idempotent. A failed Shopify catalog scan does not prevent adding the developer; the row shows the scan error and can be refreshed from its dossier.

## Verification

Repository tests cover search ranking, add/remove idempotency, automatic portfolio following, future-app following, route authentication, and rendered counts/actions.
