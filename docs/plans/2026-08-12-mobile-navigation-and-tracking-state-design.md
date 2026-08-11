# Mobile Navigation And Tracking State Design

## Goal

Make the mobile navigation compact and predictable, make the app picker use the available screen instead of leaving a large empty band, and distinguish configured GA4 tracking from tracking that has actually received data.

## Tracking State

The stored `tracking_status` remains configuration state. The integrations read model derives a display state from that value and `ga4_daily`: a configured app with no imported GA4 rows is `awaiting_data`; an app with rows is `connected`. Blocked, pending, and unknown states remain unchanged. This avoids a migration and keeps the distinction at the presentation boundary where it belongs.

The integrations table renders `Awaiting first data` with a neutral status treatment. The runbook explains that `Connected` is only earned after the first imported event.

## Mobile Header

At widths up to 620px, the header becomes a compact first row containing the shortened wordmark, hamburger button, and account avatar. The account name and secondary `Analytics` label are hidden. The app selector remains a full-width second row so long app names retain a usable target and do not compete with navigation controls.

## Mobile App Picker

The app picker becomes a near-full-height mobile sheet, positioned against the safe top edge and the bottom of the dynamic viewport. Its title and search input stay fixed while the option list owns the remaining scroll area. This removes the large empty backdrop above the picker and keeps search results accessible when the software keyboard reduces the viewport.

The desktop anchored dialog is unchanged. Existing native dialog semantics, focus behavior, keyboard selection, and no-JavaScript select fallback remain intact.

## Verification

Add server-side tests for the derived tracking state and rendered label. Add markup assertions for the mobile-specific hooks. Verify desktop and mobile screenshots with Playwright at an iPhone-sized viewport, including an open app picker and a long selected app name. Confirm no horizontal overflow and run the full test suite.
