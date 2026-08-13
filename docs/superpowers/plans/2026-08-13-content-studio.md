# Mantle Content Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a controlled Content Studio that produces evidence-backed SEO articles and YouTube scripts, generates Newcraft-style media, prevents content cannibalization, and publishes approved work through WordPress.

**Architecture:** Add a dedicated content domain beside Research, using immutable versions and explicit stage transitions. Keep external boundaries in focused OpenRouter, source-fetch, media, and WordPress clients; reuse the existing Backblaze S3 configuration and server-rendered FastAPI/Jinja interface. Every publish action is operator initiated, quality gated, and idempotent.

**Tech Stack:** Python 3.13, FastAPI, Jinja2, PostgreSQL/psycopg, httpx, BeautifulSoup, boto3/Backblaze B2, OpenRouter structured chat and Image APIs, WordPress REST API, pytest, Playwright.

---

## File Map

- Create `src/app_dashboard/migrations/029_content_studio.sql`: content profiles, inventory, projects, versions, sources, links, quality checks, media, runs, and publications.
- Create `src/app_dashboard/content_profiles.py`: validate and persist per-app facts, claims, URLs, languages, style, and WordPress relation.
- Create `src/app_dashboard/content_inventory.py`: parse and synchronize sitemap/pages, extract content, and compute deterministic overlap candidates.
- Create `src/app_dashboard/content_projects.py`: project lifecycle, immutable versions, sources, links, and accepted-stage pointers.
- Create `src/app_dashboard/content_ai.py`: OpenRouter boundary, schemas, editorial policy, prompt assembly, and staged generation.
- Create `src/app_dashboard/content_quality.py`: deterministic checks and structured editorial-review persistence.
- Create `src/app_dashboard/content_media.py`: Newcraft style profiles, OpenRouter Image API boundary, content-addressed B2 objects, and media selection.
- Create `src/app_dashboard/wordpress.py`: authenticated WordPress REST boundary and Gutenberg payloads.
- Create `src/app_dashboard/templates/content_index.html`: searchable project table and synchronization status.
- Create `src/app_dashboard/templates/content_form.html`: project/app-profile forms.
- Create `src/app_dashboard/templates/content_project.html`: staged editor, evidence, quality, media, and publication workspace.
- Create `tests/test_content_profiles.py`, `tests/test_content_inventory.py`, `tests/test_content_projects.py`, `tests/test_content_ai.py`, `tests/test_content_quality.py`, `tests/test_content_media.py`, and `tests/test_wordpress.py`.
- Modify `src/app_dashboard/config.py`, `src/app_dashboard/web.py`, `src/app_dashboard/templates/base.html`, `.env.example`, `tests/test_migrations.py`, `tests/test_web.py`, and `docs/configuration.md`.

## Task 1: Persist the Content Domain

**Files:**
- Create: `src/app_dashboard/migrations/029_content_studio.sql`
- Modify: `tests/test_migrations.py`

- [ ] **Step 1: Write failing schema assertions**

Add assertions for the eleven tables from the file map and verify PostgreSQL rejects unsupported channels, languages with invalid tags, mutable duplicate version numbers, and publication rows without a project.

```python
assert table_names(db) >= {
    "app_content_profiles", "content_inventory", "content_projects",
    "content_versions", "content_sources", "content_links",
    "content_style_profiles", "content_media", "content_quality_checks",
    "content_runs", "content_publications",
}
```

- [ ] **Step 2: Confirm the migration test fails**

Run: `uv run pytest tests/test_migrations.py -q`

Expected: FAIL because migration 029 and its tables do not exist.

- [ ] **Step 3: Add migration 029**

Use foreign keys to `apps(id)`, append-only `content_versions`, `jsonb` only for versioned structured payloads, and explicit checks:

```sql
channel text not null check (channel in ('seo_article','youtube')),
stage text not null check (stage in (
  'idea','brief','outline','draft','review','media','ready','published','archived'
)),
overlap_status text not null default 'unchecked' check (overlap_status in (
  'unchecked','clear','differentiate','update_existing','blocked'
))
```

Create unique constraints for `(project_id, stage, version_number)`, inventory canonical URL, source digest, media digest, and one active WordPress publication per project. Add indexes for project filters, inventory sync, versions, runs, and publication status.

- [ ] **Step 4: Run migrations twice**

Run: `uv run pytest tests/test_migrations.py -q`

Expected: PASS on a clean database and on the existing idempotency test.

- [ ] **Step 5: Commit**

```bash
git add src/app_dashboard/migrations/029_content_studio.sql tests/test_migrations.py
git commit -m "Store reproducible content production state"
```

## Task 2: Manage Evidence-backed App Profiles

**Files:**
- Create: `src/app_dashboard/content_profiles.py`
- Modify: `src/app_dashboard/integrations.py`
- Modify: `src/app_dashboard/templates/integration_form.html`
- Test: `tests/test_content_profiles.py`
- Test: `tests/test_integrations.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write failing validation and route tests**

Cover normalized BCP-47 languages, HTTPS-only Newcraft/Shopify URLs, unique fact labels, non-empty claim evidence, WordPress related-app ID, default language membership, and no secret values rendered by Management.

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `uv run pytest tests/test_content_profiles.py tests/test_integrations.py tests/test_web.py -q`

Expected: FAIL because content profiles and fields do not exist.

- [ ] **Step 3: Implement profile persistence**

Expose a typed record and `save_content_profile(conn, app_id, values)`. Store facts, allowed claims, forbidden claims, audiences, objections, source URLs, and illustration profile as validated JSON arrays. Never read credentials here.

- [ ] **Step 4: Add a Content profile section to the app form**

Use the existing management form patterns. Provide fields for pillar URL, Shopify listing URL, related-app ID, default/supported languages, facts, allowed/forbidden claims, audiences, objections, source URLs, and style-profile selection. Add concise descriptions stating that only verified facts may be entered.

- [ ] **Step 5: Run focused tests**

Run: `uv run pytest tests/test_content_profiles.py tests/test_integrations.py tests/test_web.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/app_dashboard/content_profiles.py src/app_dashboard/integrations.py src/app_dashboard/templates/integration_form.html tests/test_content_profiles.py tests/test_integrations.py tests/test_web.py
git commit -m "Give content generation verified app context"
```

## Task 3: Synchronize the WordPress Content Inventory

**Files:**
- Create: `src/app_dashboard/content_inventory.py`
- Modify: `src/app_dashboard/config.py`
- Modify: `.env.example`
- Modify: `docs/configuration.md`
- Test: `tests/test_content_inventory.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write parser and safety tests**

Use local sitemap and article fixtures. Assert namespace-aware sitemap parsing, canonical URL normalization, title/headings/text extraction, unchanged digest preservation, removal marking only after a complete import, response-size bounds, and rejection of redirects outside configured hosts.

- [ ] **Step 2: Confirm focused tests fail**

Run: `uv run pytest tests/test_content_inventory.py tests/test_config.py -q`

Expected: FAIL because inventory settings and synchronizer do not exist.

- [ ] **Step 3: Add bounded settings**

Add `content_sitemap_url`, `content_allowed_hosts`, `content_fetch_timeout_seconds`, `content_page_max_bytes`, and `content_inventory_max_age_hours`. Validate HTTPS outside localhost and bounds of 1-30 seconds, 64 KB-5 MB, and 1-168 hours.

- [ ] **Step 4: Implement transactional synchronization**

Fetch the sitemap and pages with one `httpx.Client`, bounded redirects, a descriptive user agent, and explicit content checks. Parse HTML with BeautifulSoup, strip navigation/scripts/styles, and upsert only after every requested page has a valid result. Record a `content_runs` row for success or a safe failure.

- [ ] **Step 5: Implement deterministic overlap candidates**

Normalize title, slug, H2/H3 text, and target query into token sets. Return the strongest candidates using weighted Jaccard similarity and exact phrase boosts; do not classify intent in this module.

- [ ] **Step 6: Run focused tests and commit**

Run: `uv run pytest tests/test_content_inventory.py tests/test_config.py -q`

```bash
git add src/app_dashboard/content_inventory.py src/app_dashboard/config.py .env.example docs/configuration.md tests/test_content_inventory.py tests/test_config.py
git commit -m "Index existing content before proposing new work"
```

## Task 4: Add Projects, Sources, Versions, and Agent Briefs

**Files:**
- Create: `src/app_dashboard/content_projects.py`
- Test: `tests/test_content_projects.py`

- [ ] **Step 1: Write failing domain tests**

Test project creation with English default, explicit Dutch, channel validation, immutable monotonically numbered versions, accepted-version pointers, source selection, overlap resolution, and a deterministic agent brief containing policy version, app facts, sources, inventory conflicts, language, and requested output schema.

- [ ] **Step 2: Confirm failure**

Run: `uv run pytest tests/test_content_projects.py -q`

Expected: FAIL because the project repository does not exist.

- [ ] **Step 3: Implement the project repository**

Keep SQL in focused functions: `create_project`, `project_detail`, `list_projects`, `add_version`, `accept_version`, `set_sources`, `set_links`, and `resolve_overlap`. Use database transactions for operations that change a pointer and append a version.

- [ ] **Step 4: Build the canonical agent brief**

Produce plain Markdown with sections for objective, language, reader, BOFU intent, verified facts, forbidden claims, existing-content conflicts, required internal links, format contract, editorial rules, and expected JSON fields. Never include environment values or unrelated database data.

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/test_content_projects.py -q`

```bash
git add src/app_dashboard/content_projects.py tests/test_content_projects.py
git commit -m "Make content work staged and reproducible"
```

## Task 5: Build the Content Index and Creation Flow

**Files:**
- Create: `src/app_dashboard/templates/content_index.html`
- Create: `src/app_dashboard/templates/content_form.html`
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] **Step 1: Add failing authenticated route tests**

Cover `GET /content`, `GET /content/new`, `POST /content`, filters, English default, Dutch selection, empty state, sitemap status, and rejection of an app without a content profile. Assert unauthenticated requests redirect to login.

- [ ] **Step 2: Confirm route tests fail**

Run: `uv run pytest tests/test_web.py -q -k content`

Expected: FAIL with missing routes.

- [ ] **Step 3: Add routes and templates**

Render a work-focused table with app, title/query, language, channel, overlap, stage, modified time, and WordPress status. Use existing macros and controls; add `Content` after Research in the sidebar. The creation form chooses app, language, channel, and supplied topic versus opportunity generation.

- [ ] **Step 4: Add explicit inventory sync**

Add `POST /content/inventory/sync`, protected by the existing browser form/origin checks. Run synchronization synchronously for the first increment and redirect with a safe status message; background scheduling comes only after measured runtime justifies it.

- [ ] **Step 5: Run route tests and commit**

Run: `uv run pytest tests/test_web.py -q -k content`

```bash
git add src/app_dashboard/templates/content_index.html src/app_dashboard/templates/content_form.html src/app_dashboard/web.py src/app_dashboard/templates/base.html tests/test_web.py
git commit -m "Add the operator-controlled content workspace"
```

## Task 6: Integrate OpenRouter with Structured Stages

**Files:**
- Create: `src/app_dashboard/content_ai.py`
- Modify: `src/app_dashboard/config.py`
- Modify: `.env.example`
- Modify: `docs/configuration.md`
- Test: `tests/test_content_ai.py`

- [ ] **Step 1: Write failing boundary tests**

Mock `httpx` and assert bearer authentication, model alias selection, timeouts, safe errors, no secret logging, JSON-only response parsing, schema rejection, usage capture, and that prompts contain only the selected evidence pack.

- [ ] **Step 2: Write failing stage-contract tests**

Define and test concrete schemas for `ideas`, `brief`, `outline`, `seo_draft`, `youtube_draft`, `editorial_review`, and `revision`. Each idea must include target intent, buyer problem, app fit, evidence IDs, internal-link candidates, and overlap decision.

- [ ] **Step 3: Implement settings and client**

Add `openrouter_api_key`, separate model settings for generation, review, and images, timeout, and optional site/app headers. `OpenRouterClient.complete(schema, messages, model)` must send strict `json_schema` output plus `provider.require_parameters=true`, and return a typed result with payload, model, request ID, token usage, duration, and redacted error.

- [ ] **Step 4: Implement the versioned editorial policy**

Create `EDITORIAL_POLICY_VERSION = "2026-08-13.1"` and encode the approved specificity, evidence, formatting, internal-link, anti-slop, emoji, and em-dash rules. Direct generation and copied briefs both call the same policy builder.

- [ ] **Step 5: Implement stage runners**

Each runner creates a `content_runs` row, calls OpenRouter, validates the payload, appends a `content_versions` row, and closes the run. A failure records the safe error and leaves the previously accepted version intact.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/test_content_ai.py tests/test_config.py -q`

```bash
git add src/app_dashboard/content_ai.py src/app_dashboard/config.py .env.example docs/configuration.md tests/test_content_ai.py tests/test_config.py
git commit -m "Generate marketing content through verified stages"
```

## Task 7: Enforce Quality and Cannibalization Decisions

**Files:**
- Create: `src/app_dashboard/content_quality.py`
- Test: `tests/test_content_quality.py`

- [ ] **Step 1: Write failing deterministic-check tests**

Cover required pillar/listing links, two-to-four internal links, HTTPS/live internal URLs, anchor variation, 15-20 word excerpt, unresolved placeholders, emoji, em dashes, duplicate paragraphs, keyword stuffing, unsupported external URLs, and required SEO/YouTube structure.

- [ ] **Step 2: Write failing publication-gate tests**

Assert publication is blocked by stale inventory, unresolved overlap, failed critical checks, no accepted draft, missing media, or a review that cites unsupported claims. Draft generation remains allowed while checks are incomplete.

- [ ] **Step 3: Implement checks and gate**

Return named findings with `pass`, `warning`, or `block`, plus actionable evidence. Persist all findings for the accepted version. Do not mutate the content while checking it.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest tests/test_content_quality.py -q`

```bash
git add src/app_dashboard/content_quality.py tests/test_content_quality.py
git commit -m "Block weak or overlapping content from publication"
```

## Task 8: Build the Staged Content Workspace

**Files:**
- Create: `src/app_dashboard/templates/content_project.html`
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] **Step 1: Add failing workspace tests**

Cover detail rendering, stage tabs, evidence rail, conflict actions, version history, accepted output, agent-brief download/copy payload, generation POST routes, manual edits as a new version, quality results, and preserved content after a failed run.

- [ ] **Step 2: Confirm failures**

Run: `uv run pytest tests/test_web.py -q -k content_project`

Expected: FAIL with missing routes/templates.

- [ ] **Step 3: Add stage actions**

Implement browser-form routes for idea, brief, outline, draft, review, revision, accept, manual edit, source selection, overlap resolution, and quality check. Validate allowed stage transitions in the domain module, not the template.

- [ ] **Step 4: Render the workspace**

Use one main editing column and one compact evidence/status rail on desktop; stack actions, evidence, and editor on mobile. Show runs and versions as terse history, not nested cards. Keep a stable action bar for `Generate`, `Save version`, `Copy agent brief`, and `Run checks`.

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/test_web.py -q -k content`

```bash
git add src/app_dashboard/templates/content_project.html src/app_dashboard/web.py src/app_dashboard/templates/base.html tests/test_web.py
git commit -m "Expose content evidence and revisions in one workspace"
```

## Task 9: Generate and Store Newcraft-style Media

**Files:**
- Create: `src/app_dashboard/content_media.py`
- Modify: `src/app_dashboard/object_storage.py`
- Modify: `src/app_dashboard/config.py`
- Test: `tests/test_content_media.py`
- Test: `tests/test_object_storage.py`

- [ ] **Step 1: Write failing style and storage tests**

Assert immutable style versions, reference-object membership, role-specific aspect ratios, prompts without rendered headline text, supported PNG/JPEG/WebP output, SHA-256 deduplication under `content/<prefix>/<digest>`, editable alt text, and only one selected medium per role.

- [ ] **Step 2: Write failing OpenRouter Image API tests**

Mock `POST https://openrouter.ai/api/v1/images` and assert bearer authentication, configured model, aspect ratio, output format, reference inputs, request timeout, cost/model recording, base64 response bounds, MIME inspection, and safe failure. The adapter must return bytes and metadata; it must not write database state itself.

- [ ] **Step 3: Generalize content-addressed B2 storage**

Extract the shared B2 client and validated upload primitive without changing Research behavior. Keep private objects, AES256 server-side encryption, SHA metadata, and presigned reads.

- [ ] **Step 4: Implement media service**

Build prompts from the selected content brief and style version, store generated bytes, append media records, allow selection and alt-text edits, and record runs. Generate clean artwork; render optional YouTube text overlays in a deterministic later operation.

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/test_content_media.py tests/test_object_storage.py -q`

```bash
git add src/app_dashboard/content_media.py src/app_dashboard/object_storage.py src/app_dashboard/config.py tests/test_content_media.py tests/test_object_storage.py
git commit -m "Preserve Newcraft media as reproducible assets"
```

## Task 10: Implement the WordPress REST Boundary

**Files:**
- Create: `src/app_dashboard/wordpress.py`
- Modify: `src/app_dashboard/config.py`
- Modify: `.env.example`
- Modify: `docs/configuration.md`
- Test: `tests/test_wordpress.py`

- [ ] **Step 1: Write failing request-contract tests**

Mock WordPress responses and assert Basic auth, `marketing-post`, title, slug, Gutenberg content, 15-20 word excerpt, status, scheduled date, ACF `related_apps`, media multipart upload, `featured_media`, update endpoint, timeout, and redacted HTTP errors.

- [ ] **Step 2: Write failing idempotency tests**

Assert an unchanged retry reuses the stored WordPress post/media result, create never updates an unrelated post, and update requires an explicit WordPress ID selected on the project.

- [ ] **Step 3: Add WordPress settings**

Configure `wordpress_site_url`, `wordpress_username`, `wordpress_application_password`, `wordpress_post_type`, and timeout. Validate a complete configuration and expose only `wordpress_configured` to views.

- [ ] **Step 4: Implement client and Gutenberg renderer**

Use structured HTML parsing rather than shell string substitution. Render supported headings, paragraphs, lists, tables, quotes, images, and embeds as Gutenberg blocks. Upload selected media first, then create/update the post and persist its ID, URL, status, request hash, and safe response metadata.

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/test_wordpress.py tests/test_config.py -q`

```bash
git add src/app_dashboard/wordpress.py src/app_dashboard/config.py .env.example docs/configuration.md tests/test_wordpress.py tests/test_config.py
git commit -m "Publish approved content through WordPress reliably"
```

## Task 11: Add Media and Publication UI

**Files:**
- Modify: `src/app_dashboard/templates/content_project.html`
- Modify: `src/app_dashboard/web.py`
- Modify: `src/app_dashboard/templates/base.html`
- Test: `tests/test_web.py`

- [ ] **Step 1: Add failing UI route tests**

Cover style selection, generate/regenerate, full-size preview, media selection, alt-text save, WordPress test, draft, scheduled post, publish confirmation, update-existing confirmation, stale-inventory refusal, quality refusal, and successful publication state.

- [ ] **Step 2: Confirm failures**

Run: `uv run pytest tests/test_web.py -q -k 'content_media or content_publish'`

Expected: FAIL with missing actions.

- [ ] **Step 3: Add media controls**

Render selectable thumbnails with a full-size dialog, generation run state, role, dimensions, style version, alt text, and selected status. Preserve accessible buttons and keyboard-close behavior.

- [ ] **Step 4: Add publication controls**

Show title, slug, excerpt, related app, featured image, target status, schedule, and target WordPress post. `Publish` and `Update existing` open a confirmation dialog with app, language, title, URL, and status before submitting.

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/test_web.py -q -k content`

```bash
git add src/app_dashboard/templates/content_project.html src/app_dashboard/web.py src/app_dashboard/templates/base.html tests/test_web.py
git commit -m "Make publication explicit and reviewable"
```

## Task 12: End-to-end Verification and Deployment

**Files:**
- Modify only files required by verified defects.

- [ ] **Step 1: Run focused domain and boundary tests**

Run:

```bash
uv run pytest -q \
  tests/test_content_profiles.py tests/test_content_inventory.py \
  tests/test_content_projects.py tests/test_content_ai.py \
  tests/test_content_quality.py tests/test_content_media.py \
  tests/test_wordpress.py tests/test_web.py
```

Expected: PASS.

- [ ] **Step 2: Run the complete suite and compilation**

Run:

```bash
uv run pytest -q
uv run python -m compileall -q src
git diff --check
```

Expected: all tests pass, compilation succeeds, and no whitespace errors appear.

- [ ] **Step 3: Run a local E2E with fakes**

Start Mantle against the test fixture providers. With Playwright at 1440x900 and 390x844, complete:

```text
Create EN SEO project -> generate ideas -> choose idea -> brief -> outline -> draft
-> quality check -> generate/select image -> WordPress draft -> retry unchanged draft
```

Verify the retry retains one WordPress post and the UI remains usable without horizontal page overflow.

- [ ] **Step 4: Run a production-safe WordPress smoke test**

Create one clearly named draft, verify its Gutenberg blocks, ACF app relation, featured image, excerpt, and preview URL in WordPress, then archive/delete the smoke draft. Do not use direct publish for the smoke test.

- [ ] **Step 5: Deploy and verify**

Push `master` to `fork` and `dokku-mantle`, follow the health checks, open `/content`, test WordPress connectivity, and verify no secret appears in application logs or rendered HTML.

- [ ] **Step 6: Record operational ownership**

Add the Content Studio environment variables, sitemap sync procedure, provider failure handling, and WordPress recovery steps to `docs/deploy.md` before considering the feature complete.
