# Table Sorting And Today Design

## Goal

Make every meaningful data column in the ASO and focused Discover tables sortable, and add a `Today` period preset using Amsterdam calendar semantics.

## Scope

- Add `Today` to the shared period presets. It starts at midnight in `Europe/Amsterdam` and ends at the current time.
- Add server-side sorting to every ASO data table:
  - portfolio
  - keywords
  - install sources
  - listing changes
  - keyword research
- Add server-side sorting to the focused Discover tables for new launches and listing updates.
- Keep command-only columns such as `Actions` unsortable because they do not represent data.

## Interaction

Column labels are links. The active column shows its direction. Clicking the active column reverses the direction; clicking another column starts with that column's natural default direction. Filters, period selection, app scope, view, and sort state remain in the URL so browser navigation and pagination remain predictable.

## Architecture

Each report owns an explicit sort-key whitelist. URL values never become raw SQL or unchecked attribute names. ASO rows are sorted after their bounded reports are built. Discover ordering is applied in SQL before `limit` and `offset`, ensuring correct order across every result page.

`Today` is added to the existing period resolver rather than implemented only in the template. This keeps date boundaries, comparisons, and query serialization consistent with the other presets.

## Nulls And Stability

Missing values sort after real values in both directions. Every sort has a stable secondary key, such as app name or event timestamp, so rows do not jump between requests with equal primary values.

## Verification

- Unit tests cover Amsterdam `Today` boundaries, including the query preset.
- ASO tests cover supported keys, direction toggling, missing values, and table-specific fields.
- Discover report tests prove ordering happens before pagination and reject unknown sort values.
- Route/template tests verify sortable links preserve filters and show active direction.
- The full suite and desktop/mobile browser checks verify the final interaction.
