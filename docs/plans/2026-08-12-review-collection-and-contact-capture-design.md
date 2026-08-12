# Review Collection and Contact Capture Design

## Goal

Build one reusable Mantle service that helps every Newcraft Shopify app ask for
an App Store review after a real success moment, while capturing every staff
member who uses the app. Book Importer is the first integration. The flow sends
no email: the embedded app uses Shopify's native Reviews API.

## Product rules

- Capture contacts on every authenticated app session, independently of review
  eligibility.
- Keep the shop contact and individual staff contacts separate.
- Identify a staff contact by `(app, shop, Shopify user ID)`, never by email.
  Names, email addresses and roles are mutable attributes of that identity.
- Preserve multiple staff members for the same shop. A later login updates only
  that staff member's `last_seen_at` and current profile.
- Ask for a review only after a configured, meaningful success event. Book
  Importer's pilot event is a successfully created or updated Shopify book
  product, not a lookup, failed import or page visit.
- Do not infer that a review was submitted merely because Shopify displayed its
  modal. Public review scraping remains the source for actual review content.
- Never offer an incentive or block functionality on leaving a review.

## Architecture

### Book Importer: trusted Shopify boundary

Every embedded request already carries a Shopify session token. Book Importer
validates that token through the existing Shopify authentication stack. It uses
Shopify token exchange for an online access token response and reads the
`associated_user` object. That response supplies the stable user ID, verified
email status, first and last name, locale, account-owner flag and collaborator
flag. The raw Shopify access token is never sent to or stored by Mantle.

Book Importer synchronizes this contact server-to-server to Mantle once per
authenticated browser session. A short local throttle avoids a network request
on every Inertia navigation without changing the meaning of `last_seen_at`.
Failures are logged and must not block the merchant's workflow.

After a successful import, Book Importer posts the configured product-usage
event to Mantle. When the success response says a review request is eligible,
the next embedded response exposes a short-lived, single-use review decision to
the frontend. The frontend calls `shopify.reviews.request()` once and sends the
returned `success`, `code` and `message` to Book Importer's backend. Book
Importer reports that outcome server-to-server to Mantle. The shared Mantle
secret is never shipped to the browser.

### Mantle: policy and history boundary

Mantle extends its existing per-app usage ingestion contract rather than adding
an unrelated client SDK. Authenticated app endpoints accept:

1. a contact upsert tied to a Shopify shop GID and Shopify user ID;
2. product usage events;
3. the result of a previously issued review decision.

All three use the app's existing server-side usage token. Mantle validates and
caps every field, stores events idempotently and evaluates review eligibility
transactionally so two simultaneous success events cannot issue two prompts.

Review policy lives in database-owned app configuration:

- enabled;
- trigger event;
- minimum successful trigger count;
- minimum hours since install (at least 24);
- retry days after merchant cancellation (default 90);
- optional operator suppression.

Mantle issues an opaque decision ID with a short expiry. An outcome is accepted
only once for that app, shop and decision. A decision can become `shown`,
`temporarily_declined`, `cancelled`, `already_reviewed`, `ineligible`, `failed`
or `expired`.

## Retry policy

- `success`: Shopify displayed the modal. Do not ask again for at least 60 days.
- `cancelled`: wait 90 days and require a later success event.
- `recently-installed`: retry after the shop has been installed for 24 hours.
- `cooldown-period`: wait 60 days from the attempt unless a later Shopify
  response gives a stricter boundary.
- `annual-limit-reached`: wait 365 days from the attempt.
- `mobile-app`, `already-open` and `open-in-progress`: temporary; reconsider on
  a later desktop success/session without counting a successful display.
- `already-reviewed`: permanently suppress future prompts.
- `merchant-ineligible`: suppress until an operator explicitly resets it.
- Network or unexpected failures use bounded retry and never interrupt the app.

Mantle also enforces no more than three recorded successful modal displays per
rolling 365 days, matching Shopify's documented ceiling. Shopify remains the
final eligibility authority and may decline any request.

## Data model

### Contacts

`merchant_contacts` stores `app_id`, `shop_gid`, `shopify_user_id`, name parts,
verified email, email verification state, locale, account owner, collaborator,
access level, source, `first_seen_at`, and `last_seen_at`. A partial unique index
on `(app_id, shop_gid, shopify_user_id)` applies to staff contacts. A distinct
shop-contact row uses a separate contact kind and no fabricated user ID.

### Review configuration and attempts

Review configuration columns live on `apps`, beside existing usage event
configuration. `review_prompt_attempts` stores decision ID, app/shop, triggering
usage event, issue/expiry timestamps, outcome, Shopify response code/message,
attempt time and computed next eligibility time. A per-shop suppression record
stores permanent or operator-selected `never ask` state.

No access tokens, session tokens or unverified arbitrary browser identities are
stored.

## Mantle interface

The app management form gains a **Review collection** section with trigger,
threshold, delay, retry interval and enabled state. It shows aggregate counts
for eligible shops, displayed prompts, temporary declines, suppressions and
scheduled retries.

The merchant detail page gains:

- a separate shop contact;
- every observed staff member with role and first/last seen dates;
- last success event, last review attempt, outcome and next eligible date;
- a `Never ask again` / reset control.

This is operational state, not a marketing email UI.

## Book Importer pilot

Book Importer adds one focused integration service for Mantle calls and one
frontend hook for the native modal. Successful single imports, bulk items and
scan-created products all converge on one `book_import_succeeded` event. Event
IDs derive from the persisted import record, making retries idempotent.

The first rollout can enable contact capture while leaving review prompting
disabled. Once contacts and success events are visible in Mantle, enabling the
app configuration activates prompts without another Book Importer deployment.

## Failure handling and privacy

- Contact and usage synchronization is best-effort and never blocks imports.
- Failed outbound calls are logged with app/shop identifiers but no token or
  full request body.
- Mantle rejects unverified email claims and malformed Shopify IDs.
- Contact deletion follows shop redaction/uninstall retention policy; the
  implementation must add explicit tests for removal or anonymization.
- The dashboard remains login-protected. Secrets stay in Dokku environment
  variables and never appear in HTML.

## Verification

Mantle tests cover validation, contact upserts for multiple staff, idempotency,
concurrent decisions, every Shopify response code, retries, suppression and
management/customer rendering. Book Importer tests cover token-derived staff
identity, session throttling, all import paths, best-effort failures and exactly
one native Reviews API call per issued decision. A development-store smoke test
verifies Shopify's modal before production rollout.

