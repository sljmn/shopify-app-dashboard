# Configuration

Global runtime and authentication settings live in `.env`. App identity and app-specific business
rules live in the versioned catalog at `config/apps.yml` (or `APPS_CONFIG_PATH`). Secret values
never belong in the catalog: it stores the environment-variable name that contains each secret.

## Required global settings

`DATABASE_URL`, `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`, `PUBLIC_BASE_URL`, and every Partner
token environment variable referenced by the catalog. The username and password form one shared
dashboard account; there is no signup. Changing the username invalidates existing sessions.

Create one Partner API token per organization at
`partners.shopify.com/<org-id>/settings/partner_api_clients`. Apps in the same organization share
that client and token; their cursors and data remain independent.

This fork requires a **new empty database**. Migration 011 refuses an in-place conversion because
old rows have no trustworthy app owner. Run migrations against a new database and let the first
sync replay every configured app.

## App catalog

Each app requires a stable `slug`, display `name`, Partner app GID, and `annual_plan_amounts`.
Optional fields are its App Store `listing_url`, localized listings, usage contract, and GA4 property:

```yaml
organizations:
  - partner_org_id: "123"
    name: Example Org
    token_env: SHOPIFY_PARTNER_TOKEN_123
    apps:
      - slug: example-app
        name: Example App
        partner_app_id: gid://partners/App/456
        annual_plan_amounts: ["190.00"]
        listing_url: https://apps.shopify.com/example-app
        listing_locales: [en, de, nl]
        usage:
          token_env: EXAMPLE_USAGE_TOKEN
          event_types: [settings_completed, offer_created, offer_impression]
          activation_event: offer_created
          live_event: offer_impression
        ga4:
          property_id: "123456789"
          credentials_env: SHARED_GA4_CREDENTIALS_JSON
```

`AppSubscription` has no billing interval. Annual plans are inferred by price per app, so an
omitted annual price counts at twelve times its true MRR. Changing the list requires resetting that
app's Partner cursor and replaying it.

Usage event names are also per app. The activation and live names must appear in `event_types`, or
catalog validation refuses to migrate. Ingest at `POST /ingest/usage/<app-slug>` with that app's
token.

GA4 is optional and each app keeps its own property ID. Multiple properties can reuse the same
read-only service-account environment variable; the credential JSON is never persisted. The
Traffic report asks for one selected app and never blends listing properties.
`GA4_EARLIEST_DATA` is the shared lower bound for first backfills. `listing_locales` defaults to
`[en]` and accepts language codes such as `de` or language-region codes such as `pt-BR`.

## Scope semantics

No `?app=` means All apps. Lifecycle and financial figures add app installations, so one shop
installed in two apps counts twice. `?app=<slug>` narrows the complete page. Traffic, activation,
and annotation writes require one selected app.

## Operational settings

`POLL_INTERVAL_MINUTES` controls the sync cadence and the stale thresholds. Exactly one application
instance must run APScheduler. `SESSION_SECRET` must contain at least 32 characters off localhost.
`TRUSTED_CLIENT_IP_HEADER` must name a header your proxy overwrites. `SLACK_WEBHOOK_URL` and per-app
usage/GA4 integrations are optional.
