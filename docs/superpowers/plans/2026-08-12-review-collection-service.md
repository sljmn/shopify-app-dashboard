# Review Collection Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a trustworthy central service to Mantle that captures shop and staff contacts, decides when an app may show Shopify's native review prompt, records prompt outcomes, and exposes configuration and merchant history in the dashboard.

**Architecture:** Existing authenticated usage ingestion remains the only place where product-success events enter Mantle. A focused `review_collection` module evaluates stored events against per-app policy, creates short-lived single-use decisions, and records native Shopify Reviews API outcomes; separate M2M endpoints upsert verified contacts and redact PII. The dashboard reads these tables for app configuration, aggregate review metrics, and per-merchant contact and prompt history.

**Tech Stack:** Python 3.12, FastAPI, PostgreSQL/psycopg, Jinja templates, pytest.

---

## File Map

- Create `src/app_dashboard/migrations/027_review_collection.sql`: policy columns, contacts, decisions, suppressions, constraints, and indexes.
- Create `src/app_dashboard/review_collection.py`: payload validation, contact upsert/redaction, eligibility evaluation, decision issuance, outcome recording, and reporting queries.
- Modify `src/app_dashboard/catalog.py`: carry review policy through `AppConfig` and catalog reconciliation.
- Modify `src/app_dashboard/integrations.py`: validate and persist review policy from Management.
- Modify `src/app_dashboard/usage.py`: expose stored event identities needed by the decision evaluator without coupling policy logic into usage parsing.
- Modify `src/app_dashboard/web.py`: return review decisions from usage ingestion and add contact/outcome/redaction routes.
- Modify `src/app_dashboard/customers.py`: add contacts, suppression, and attempt history to merchant detail.
- Modify `src/app_dashboard/templates/integration_form.html`: review policy controls and aggregate status.
- Modify `src/app_dashboard/templates/customer.html`: separate shop contact, staff members, and prompt history.
- Modify `src/app_dashboard/templates/base.html`: only scoped styles needed by the two existing views.
- Create `tests/test_review_collection.py`: domain rules and database behavior.
- Modify `tests/test_usage.py`, `tests/test_web.py`, `tests/test_integrations.py`, `tests/test_customers.py`, and `tests/test_migrations.py`: integration coverage.
- Modify `docs/usage-events-integration.md`: client contract and privacy/redaction behavior.

### Task 1: Persist Review Policy, Contacts, Decisions, and Suppressions

**Files:**
- Create: `src/app_dashboard/migrations/027_review_collection.sql`
- Modify: `tests/test_migrations.py`

- [ ] **Step 1: Write failing migration assertions**

Add a migration test that runs all migrations twice and asserts these columns/tables exist:

```python
assert column_names(db, "apps") >= {
    "review_prompt_enabled", "review_trigger_event",
    "review_min_success_count", "review_min_install_hours",
    "review_retry_days", "review_annual_cap",
}
assert table_names(db) >= {
    "merchant_contacts", "review_prompt_decisions", "review_prompt_suppressions",
}
```

Also assert PostgreSQL rejects an unsupported contact kind and duplicate `(app_id, shop_gid, shopify_user_id)` staff records.

- [ ] **Step 2: Run the migration test and confirm failure**

Run: `pytest tests/test_migrations.py -q`

Expected: FAIL because migration 027 and its schema do not exist.

- [ ] **Step 3: Add migration 027**

Add app policy columns with conservative defaults: disabled, no trigger, one success, 24 install hours, 90 retry days, and annual cap 3. Create:

```sql
create table merchant_contacts (
    id bigserial primary key,
    app_id bigint not null references apps(id) on delete cascade,
    shop_gid text not null,
    shop_domain text,
    kind text not null check (kind in ('shop', 'staff')),
    shopify_user_id text,
    first_name text,
    last_name text,
    email text,
    email_verified boolean not null default false,
    locale text,
    account_owner boolean,
    collaborator boolean,
    access_level text,
    first_seen_at timestamptz not null,
    last_seen_at timestamptz not null,
    updated_at timestamptz not null default now()
);
```

Use partial unique indexes for one shop contact and one row per stable Shopify user ID. Store decisions with `decision_id`, source `event_id`, issued/expiry times, outcome, Shopify code/message, response time, and `next_eligible_at`; make `(app_id, shop_gid, event_id)` unique. Store explicit merchant suppression separately with reason and operator audit timestamps.

- [ ] **Step 4: Run migration tests**

Run: `pytest tests/test_migrations.py -q`

Expected: PASS, including the second migration run.

- [ ] **Step 5: Commit the schema**

```bash
git add src/app_dashboard/migrations/027_review_collection.sql tests/test_migrations.py
git commit -m "Store review policy and merchant contact history safely"
```

### Task 2: Implement Strict Contact Capture and Redaction

**Files:**
- Create: `src/app_dashboard/review_collection.py`
- Create: `tests/test_review_collection.py`

- [ ] **Step 1: Write failing payload and upsert tests**

Cover these cases explicitly:

```python
def test_unverified_staff_email_is_not_stored(db, test_app): ...
def test_same_staff_member_updates_last_seen_without_duplicate(db, test_app): ...
def test_two_staff_members_for_one_shop_are_preserved(db, test_app): ...
def test_shop_contact_and_staff_contact_are_separate(db, test_app): ...
def test_redaction_deletes_pii_but_keeps_aggregate_decisions(db, test_app): ...
def test_contact_payload_rejects_unknown_fields_and_invalid_gid(): ...
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `pytest tests/test_review_collection.py -q`

Expected: collection error because `app_dashboard.review_collection` is missing.

- [ ] **Step 3: Implement bounded contact parsing**

Define immutable payload objects and explicit limits. Require `gid://shopify/Shop/<digits>`, `kind`, and ISO timestamps. Require a stable numeric `shopify_user_id` for staff. Accept an email only when `email_verified is True`; normalize domains and emails to lowercase. Reject nested or unknown data instead of silently retaining it.

- [ ] **Step 4: Implement idempotent upsert and PII redaction**

Use `INSERT ... ON CONFLICT ... DO UPDATE` so `first_seen_at` never moves forward and `last_seen_at` never moves backward. Redaction deletes all `merchant_contacts` for `(app_id, shop_gid)` and clears free-text response messages while preserving decision status/counts for analytics.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_review_collection.py -q`

Expected: PASS for parsing, multi-staff upsert, and redaction.

- [ ] **Step 6: Commit contact storage**

```bash
git add src/app_dashboard/review_collection.py tests/test_review_collection.py
git commit -m "Capture verified merchant contacts without collapsing staff"
```

### Task 3: Implement Deterministic Review Eligibility

**Files:**
- Modify: `src/app_dashboard/review_collection.py`
- Modify: `tests/test_review_collection.py`

- [ ] **Step 1: Write failing eligibility tests**

Use a fixed UTC clock and cover: disabled app, wrong event, fewer than minimum successes, install younger than 24 hours, active trial (allowed), operator suppression, already reviewed, cooldown, annual cap, expired unreported decision, duplicate event, and an eligible event. Assert that only the eligible case produces a decision.

- [ ] **Step 2: Run the eligibility tests and confirm failure**

Run: `pytest tests/test_review_collection.py -q -k eligibility`

Expected: FAIL because the evaluator is not implemented.

- [ ] **Step 3: Add the policy evaluator**

Implement:

```python
@dataclass(frozen=True)
class ReviewDecision:
    decision_id: str
    expires_at: datetime

def issue_review_decision(
    conn, app: AppConfig, *, shop_gid: str, event_id: str,
    event_type: str, now: datetime,
) -> ReviewDecision | None:
    ...
```

Lock the shop row with `FOR UPDATE`, count stored trigger events, inspect installation age, suppression, prior outcomes, and rolling successful displays. Generate the opaque ID with `secrets.token_urlsafe(32)`, expire it after 15 minutes, insert it transactionally, and rely on the unique source-event key for retry safety.

- [ ] **Step 4: Encode retry dates centrally**

Map Shopify result codes exactly:

```python
RETRY_AFTER = {
    "cancelled": timedelta(days=90),
    "cooldown-period": timedelta(days=60),
    "annual-limit-reached": timedelta(days=365),
    "recently-installed": timedelta(hours=24),
}
PERMANENT = {"already-reviewed", "merchant-ineligible"}
TRANSIENT = {"mobile-app", "already-open", "open-in-progress"}
```

Successful modal display uses 60 days; transient outcomes wait for a later success event without counting as a display. The database remains authoritative and Shopify remains the final eligibility authority.

- [ ] **Step 5: Run all domain tests**

Run: `pytest tests/test_review_collection.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the decision engine**

```bash
git add src/app_dashboard/review_collection.py tests/test_review_collection.py
git commit -m "Issue native review prompts only after verified product success"
```

### Task 4: Return Decisions from Usage Ingestion and Record Outcomes

**Files:**
- Modify: `src/app_dashboard/usage.py`
- Modify: `src/app_dashboard/web.py`
- Modify: `tests/test_usage.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing API contract tests**

Add tests proving that an authenticated trigger event returns:

```json
{
  "received": 1,
  "stored": 1,
  "duplicates": 0,
  "rate_limited": 0,
  "review_prompt": {
    "decision_id": "opaque",
    "expires_at": "2026-08-12T12:15:00Z"
  }
}
```

Also cover duplicate events returning no new decision, invalid outcome decision IDs returning 404, expired IDs returning 409, repeated outcome posts being idempotent, invalid tokens returning 401 before body buffering, and contact/redaction endpoint authorization.

- [ ] **Step 2: Run the API tests and confirm failure**

Run: `pytest tests/test_web.py -q -k 'review or contact or usage'`

Expected: FAIL because the routes and response field are absent.

- [ ] **Step 3: Preserve stored event identities**

Change the internal usage ingest result to include the successfully inserted `(shop_gid, event_id, event_type)` records for policy evaluation while keeping the public counters unchanged. Do not evaluate duplicate or rate-limited events.

- [ ] **Step 4: Wire four M2M operations**

Keep `_check_usage_token` and `_read_capped` as the shared boundary. Add:

```text
POST /ingest/contacts/{app_slug}
POST /ingest/contacts/{app_slug}/redact
POST /ingest/review-outcomes/{app_slug}
POST /ingest/usage/{app_slug}  (extended response)
```

Evaluate events after successful storage and return at most one decision per request. Outcome requests contain only `decision_id`, `success`, `code`, and a capped `message`; derive all shop/app context from the decision row.

- [ ] **Step 5: Run security and web tests**

Run: `pytest tests/test_usage.py tests/test_web.py tests/test_security.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the API contract**

```bash
git add src/app_dashboard/usage.py src/app_dashboard/web.py tests/test_usage.py tests/test_web.py tests/test_security.py
git commit -m "Expose authenticated review and contact ingestion contracts"
```

### Task 5: Add Review Policy to App Configuration

**Files:**
- Modify: `src/app_dashboard/catalog.py`
- Modify: `src/app_dashboard/integrations.py`
- Modify: `src/app_dashboard/templates/integration_form.html`
- Modify: `tests/test_catalog.py`
- Modify: `tests/test_integrations.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing configuration tests**

Assert disabled defaults, successful save, trigger membership in `usage_event_types`, minimum install age of 24 hours, positive retry interval, annual cap from 1 through 3, and rejection when review collection is enabled without a usage token.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/test_catalog.py tests/test_integrations.py tests/test_web.py -q -k review`

Expected: FAIL because policy fields are not loaded or saved.

- [ ] **Step 3: Extend `AppSpec`/`AppConfig` and reconciliation**

Add fields matching migration 027. Read optional catalog YAML under `review_prompt`, validate it with the same rules as Management, and include policy columns in reconciliation and `list_apps`.

- [ ] **Step 4: Extend Management persistence and UI**

Add a `Review collection` fieldset using existing form components: enabled checkbox, trigger event select, minimum success count, install wait, retry days, and annual cap. Under it show eligible, shown, temporarily declined, stopped, and scheduled aggregate counts from `review_collection.app_summary`.

- [ ] **Step 5: Run configuration tests**

Run: `pytest tests/test_catalog.py tests/test_integrations.py tests/test_web.py -q -k review`

Expected: PASS.

- [ ] **Step 6: Commit configuration**

```bash
git add src/app_dashboard/catalog.py src/app_dashboard/integrations.py src/app_dashboard/templates/integration_form.html tests/test_catalog.py tests/test_integrations.py tests/test_web.py
git commit -m "Make review prompting configurable per Shopify app"
```

### Task 6: Show Contacts and Review History on Merchant Detail

**Files:**
- Modify: `src/app_dashboard/customers.py`
- Modify: `src/app_dashboard/templates/customer.html`
- Modify: `src/app_dashboard/templates/base.html`
- Modify: `tests/test_customers.py`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing merchant detail tests**

Create one shop contact, two staff contacts, three decisions, and an operator suppression. Assert the response keeps contacts separate and returns chronological attempt history plus computed next-eligible date.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/test_customers.py tests/test_web.py -q -k 'contact or review_prompt'`

Expected: FAIL because customer detail has no review/contact data.

- [ ] **Step 3: Extend the customer query model**

For every app section add `shop_contact`, `staff_contacts`, `review_prompt`, and `review_attempts`. Use explicit column mappings so the existing lifecycle/payment/usage structures remain unchanged.

- [ ] **Step 4: Render operational controls**

Show shop contact separately from staff members. For staff show name, verified email, role flags, locale, first seen, and last seen. Show last success event, last attempt/result, next date, and attempt history. Add POST actions `Never ask` and `Reset` guarded by the existing authenticated CSRF flow.

- [ ] **Step 5: Verify responsive layout**

Run: `pytest tests/test_customers.py tests/test_web.py -q`

Then run the local server and inspect one populated customer at 1440px and 390px widths; expected: no horizontal page overflow, staff rows wrap, and controls stay reachable.

- [ ] **Step 6: Commit merchant operations**

```bash
git add src/app_dashboard/customers.py src/app_dashboard/templates/customer.html src/app_dashboard/templates/base.html tests/test_customers.py tests/test_web.py
git commit -m "Expose merchant contacts and review prompt history"
```

### Task 7: Document and Verify the Service End to End

**Files:**
- Modify: `docs/usage-events-integration.md`
- Modify: `README.md`

- [ ] **Step 1: Document exact client contracts**

Include request/response JSON for contact upsert, usage decision, native outcome, and redaction. State that clients must send verified Shopify identity only, never access tokens, must retain multiple staff users, and must never infer a posted review from a successful modal display.

- [ ] **Step 2: Run the full suite**

Run: `pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Run static validation**

Run the repository's existing formatter/linter command from `README.md` or CI configuration.

Expected: exit status 0 with no new violations.

- [ ] **Step 4: Commit docs and final verification**

```bash
git add docs/usage-events-integration.md README.md
git commit -m "Document native review collection integration and privacy rules"
```

