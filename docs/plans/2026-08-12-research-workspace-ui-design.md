# Research Workspace UI Design

## Purpose

Research is an internal working surface for collecting apps, decisions, notes, and evidence. The current list detail exposes every field at once and makes the page read like an unfinished form. The redesign should make existing research easy to scan while keeping edits and additions close at hand.

## Direction

Use a restrained editorial workspace within Mantle's existing visual system: clear hierarchy, compact metadata, bordered work areas only where interaction needs framing, and no decorative imagery. The memorable interaction is a list header that reads as a research dossier rather than a database record.

## Research Index

- Keep the existing type tabs and result tables.
- Replace the two plain date text inputs with the existing Flatpickr control used by Activity, Period, and ASO.
- Group each date input with a visible label and calendar icon.
- Preserve all existing query parameters and server-side date parsing.

## List Detail

- Present the list title, status, dates, app count, note count, and attachment count in a compact dossier header.
- Show description as readable text by default.
- Put title, description, and status in a native disclosure labelled `Edit details`; saving retains the existing POST route.
- Make `Add app` and `New note` the primary workspace actions.
- Replace the raw handle field with an accessible server-backed app search. Search results show app name, handle, category, and follow state; selecting a result submits its canonical handle.
- Keep apps and notes as separate full-width work sections with useful empty states.

## Responsive Behavior

- Desktop uses a two-column dossier header with actions aligned to the right.
- Tablet and mobile stack metadata, actions, filters, and edit fields.
- Search results remain attached to the app search control and use large touch targets.

## Data And Errors

- Existing research tables and write routes remain unchanged.
- Add a read-only app-search route that returns at most eight indexed apps.
- An empty query returns no results; an unknown submitted handle continues to use the existing 404 behavior.
- JavaScript enhances a normal form. The selected canonical handle is still posted through a regular browser form.

## Verification

- Test Flatpickr attributes and list-detail hierarchy in rendered HTML.
- Test app-search query limits and result fields.
- Run the research and web test suites.
- Verify index, list detail, edit disclosure, app search, and mobile layout in production with Playwright.
