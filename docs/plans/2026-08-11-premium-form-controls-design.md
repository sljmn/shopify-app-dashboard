# Premium form controls

## Goal

Make app selection fast on phones and give every select and date field a consistent, polished appearance without sacrificing native mobile ergonomics or no-JavaScript fallbacks.

## App picker

Replace the sidebar's long native app select with a dedicated picker. Its trigger displays the selected app or `All apps`. On desktop it opens a compact anchored panel; below 900px it opens as a full-width bottom sheet. The panel contains an autofocus search field and a list of app options filtered by name and slug. `All apps` remains the first option.

Selecting an item submits the existing GET form, preserving every retained query parameter. Arrow keys move through visible results, Enter selects, Escape closes, and clicking outside dismisses the picker. The original select remains in the document as a no-JavaScript fallback and as the source of truth for submitted values.

## Ordinary selects

Keep native select semantics and native mobile menus. Apply one shared control treatment across Activity, Customers, Churn, and report range forms: consistent height, spacing, border, custom chevron, hover, focus, disabled, and dark-mode states. This avoids replacing short option lists with less ergonomic custom widgets while still improving their closed appearance.

## Date fields

Vendor Flatpickr 4.6.13 into the application's static directory. Enhance Activity and annotation date inputs with an accessible desktop calendar and readable alternate text while preserving ISO `YYYY-MM-DD` form values. Flatpickr's native-mobile behavior remains enabled, so phones use their operating-system date picker.

The picker respects each input's current value, `max`, and `required` attributes. Without JavaScript, the original `type=date` input continues to work.

## Verification

Server tests pin the progressive-enhancement markup, local assets, retained query parameters, and date hooks. Browser verification covers opening, searching, selecting, keyboard behavior, clearing, date selection, no overflow, and layout stability at desktop and mobile widths.
