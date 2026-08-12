# Review Collection Playbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish an authenticated Mantle playbook for integrating contact capture and native Shopify review prompts, and centrally exclude confirmed development stores.

**Architecture:** Reuse Mantle's existing runbook presentation and management routes for the documentation. Extend the trusted shop-contact payload with Shopify's `partnerDevelopment` value, persist it on the Mantle shop record, and check it inside the transactional review-decision policy before issuing a prompt.

**Tech Stack:** FastAPI, Jinja2, PostgreSQL migrations, pytest, Rails, Shopify Admin GraphQL, Minitest.

---

### Task 1: Protect development stores in Mantle

**Files:**
- Create: `src/app_dashboard/migrations/028_shop_development_status.sql`
- Modify: `src/app_dashboard/review_collection.py`
- Test: `tests/test_review_collection.py`

- [ ] Add a failing policy test that inserts a shop with `partner_development=true` and asserts `issue_review_decision(...)` returns `None`.
- [ ] Add the nullable `shops.partner_development` column.
- [ ] Accept only a boolean `partner_development` value on trusted shop-contact payloads and persist it to the matching shop.
- [ ] Read the flag under the existing shop row lock and reject only an explicit `true` value.
- [ ] Run `pytest tests/test_review_collection.py -q` and expect all tests to pass.

### Task 2: Supply Shopify's authoritative flag from Book Importer

**Files:**
- Modify: `/Users/sh/Documents/Git/bookimport/app/jobs/shopify/after_authenticate_job.rb`
- Modify: `/Users/sh/Documents/Git/bookimport/test/jobs/shopify/after_authenticate_job_test.rb`

- [ ] Extend `ShopInstallSettings` with `plan { partnerDevelopment publicDisplayName }`.
- [ ] Return `partner_development` from the normalized settings and include it in the shop contact sent to Mantle.
- [ ] Ensure plan detection runs when Mantle contact capture is enabled even when other install defaults are already populated.
- [ ] Add tests for the GraphQL mapping and captured job payload.
- [ ] Run `bin/rails test test/jobs/shopify/after_authenticate_job_test.rb` and expect all tests to pass.

### Task 3: Publish the agent playbook

**Files:**
- Create: `src/app_dashboard/templates/review_playbook.html`
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/integrations.html`
- Modify: `src/app_dashboard/templates/integration_form.html`
- Modify: `src/app_dashboard/templates/runbook.html`
- Test: `tests/test_web.py`

- [ ] Add a failing authenticated route test for `/management/review-playbook` and its key contract sections.
- [ ] Add the protected route with current integration inventory context.
- [ ] Render architecture, payloads, outcome policy, development-store exclusion, Book Importer file map, E2E procedure, rollout, and Definition of Done.
- [ ] Cross-link the playbook from management, app editing, and the New app runbook.
- [ ] Run the focused web test and expect it to pass.

### Task 4: Verify both applications

**Files:** none

- [ ] Run `pytest tests/test_review_collection.py tests/test_web.py -q` in Mantle.
- [ ] Run the focused Book Importer job and contact tests.
- [ ] Inspect both Git diffs and confirm unrelated Book Importer working-tree files are untouched.
- [ ] Commit each repository separately with a message describing why development stores are excluded and how agents get the integration instructions.
