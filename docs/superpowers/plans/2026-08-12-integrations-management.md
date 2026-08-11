# Integrations Management Implementation Plan

**Goal:** Make PostgreSQL the editable source of truth for Partner organizations and apps, with authenticated management screens and an operational runbook.

**Architecture:** Extend the existing `organizations` and `apps` tables with lifecycle and tracking metadata. Keep secrets in environment variables and resolve them only when database rows become `AppConfig` values. Bootstrap YAML only when the database contains no catalog records; all later edits use CRUD services and are read dynamically by web and scheduler jobs.

**Tech Stack:** FastAPI, Jinja2, psycopg/PostgreSQL, pytest.

---

### Task 1: Persist operational integration metadata

**Files:**
- Create: `src/app_dashboard/migrations/014_integration_management.sql`
- Create: `src/app_dashboard/integrations.py`
- Test: `tests/test_integrations.py`

Add lifecycle, listing, tracking, archive, and update fields. Implement validated organization/app create-update-archive operations, secret-presence reporting, and activation checks.

### Task 2: Make the database own runtime configuration

**Files:**
- Modify: `src/app_dashboard/catalog.py`
- Modify: `src/app_dashboard/migrate.py`
- Modify: `src/app_dashboard/scheduler.py`
- Test: `tests/test_catalog.py`
- Test: `tests/test_scheduler.py`

Import YAML only into an empty catalog, resolve active database rows into `AppConfig`, and reload apps for every scheduled job so management changes apply without a deploy.

### Task 3: Add authenticated CRUD and runbook views

**Files:**
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/base.html`
- Create: `src/app_dashboard/templates/integrations.html`
- Create: `src/app_dashboard/templates/integration_form.html`
- Create: `src/app_dashboard/templates/runbook.html`
- Test: `tests/test_web.py`

Add navigation, list/filter, organization and app forms, archive actions, same-origin write validation, inline errors, secret status, and complete setup/verification instructions.

### Task 4: Verify the full change

Run the focused integration/catalog/scheduler/web tests, then the complete pytest suite. Inspect responsive HTML/CSS and run the app locally for a browser smoke test.
