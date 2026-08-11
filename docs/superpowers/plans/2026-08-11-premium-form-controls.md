# Premium Form Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a searchable responsive app picker, consistently styled native selects, and an enhanced responsive date picker.

**Architecture:** Keep server-rendered forms and native controls as the source of truth, then progressively enhance them from `base.html`. A small local app-picker controller handles the one high-cardinality select; shared CSS skins short native selects; self-hosted Flatpickr enhances date inputs without changing submitted ISO values.

**Tech Stack:** FastAPI, Jinja2, vanilla JavaScript, CSS, HTMX, Flatpickr 4.6.13, pytest, Playwright

---

### Task 1: Pin progressive-enhancement behavior in server tests

**Files:**
- Modify: `tests/test_web.py`

- [ ] Add assertions that the sidebar renders an app-picker trigger, search field, option buttons with app name and slug data, the retained hidden query parameters, and a native select fallback.
- [ ] Add assertions that Activity and annotation date inputs carry the shared `data-datepicker` hook and that the local Flatpickr CSS and JavaScript assets are linked.
- [ ] Run `uv run pytest tests/test_web.py -q` and verify the new assertions fail against the old markup.

### Task 2: Add the searchable responsive app picker

**Files:**
- Modify: `src/app_dashboard/templates/base.html`

- [ ] Keep the existing GET form and native select, but add a button trigger and a dialog-like picker panel populated from `active_apps`.
- [ ] Add CSS for an anchored desktop panel, a fixed mobile bottom sheet, selected/hover/focus states, a scrollable result list, and a no-results message.
- [ ] Add nonce-protected vanilla JavaScript that opens and closes the picker, filters on app name or slug, manages `aria-expanded`, supports Arrow Up/Down, Enter, Escape, outside click, and writes the chosen slug to the native select before submitting the existing form.
- [ ] Ensure the enhanced picker only replaces the fallback after JavaScript has initialized successfully.

### Task 3: Unify ordinary select styling

**Files:**
- Modify: `src/app_dashboard/templates/base.html`

- [ ] Add a shared native-select appearance with stable 42px height, custom chevron, border, shadow, hover, focus, dark-mode, and disabled states.
- [ ] Preserve compact sizing for report range controls and the dark sidebar fallback.
- [ ] Add mobile filter rules so controls become full width where needed without changing HTMX or GET behavior.

### Task 4: Vendor and initialize Flatpickr

**Files:**
- Create: `src/app_dashboard/static/vendor/flatpickr/flatpickr.min.js`
- Create: `src/app_dashboard/static/vendor/flatpickr/flatpickr.min.css`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `src/app_dashboard/templates/activity.html`
- Modify: `src/app_dashboard/templates/overview.html`

- [ ] Download Flatpickr 4.6.13 from its npm release and copy only the minified browser JavaScript and CSS into the static vendor directory.
- [ ] Link both assets from `base.html` using same-origin `/static` URLs.
- [ ] Mark date inputs with `data-datepicker` and initialize them with `altInput: true`, `altFormat: "j M Y"`, `dateFormat: "Y-m-d"`, and native-mobile behavior enabled.
- [ ] Reinitialize enhanced controls after HTMX swaps without creating duplicate instances.

### Task 5: Verify behavior and layout

**Files:**
- Modify: `tests/test_web.py`

- [ ] Run `uv run pytest tests/test_web.py -q` and then `uv run pytest -q`.
- [ ] Start the local app with its existing environment and open it through Playwright.
- [ ] At desktop and mobile widths, verify app search, app selection, keyboard operation, date selection, ordinary select appearance, and absence of overflow or overlapping controls.
- [ ] Capture screenshots for both widths and inspect them before committing.
