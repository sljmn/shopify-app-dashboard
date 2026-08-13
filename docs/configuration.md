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

### Owned ASO setup

1. Create one Google Cloud service account and a JSON key for it.
2. In every GA4 property used by a Shopify App Store listing, open **Admin > Property access
   management** and grant the service-account email the read-only **Viewer** role.
3. Store the complete JSON as one deployment secret, for example
   `SHARED_GA4_CREDENTIALS_JSON`. Do not put the JSON in `config/apps.yml`.
4. Add the verified numeric property ID to each app and reuse that environment-variable name:

```yaml
listing_locales: [en, de, nl]
ga4:
  property_id: "123456789"
  credentials_env: SHARED_GA4_CREDENTIALS_JSON
```

5. Deploy, open the ASO page, and run **Fetch all data again**. A full refresh asks GA4 only for
   history still inside the property's retention window. The daily job rewrites the latest seven
   days because GA4 can process recent rows late.

Mantle queries GA4 metadata first and stores separate compatibility states for keyword reporting
and merchant attribution. `ready`, `partial`, `unsupported`, and `failed` have different meanings;
an empty successful report is not treated as an API failure. The exact Shopify dimensions depend
on what that property actually receives. Mantle does not synthesize rankings or attribution when
the dimensions are absent.

The Partner API remains authoritative for installs and uninstalls. GA4 attribution can be missing
because of consent, blockers, retention, or an absent shop-domain dimension. Compare attributed
install rows against Partner installs to measure coverage; do not expect them to reconcile to
100%.

For Dokku, set the shared secret without echoing it into shell history, then redeploy:

```bash
dokku config:set --no-restart mantle SHARED_GA4_CREDENTIALS_JSON="$(cat service-account.json)"
git push dokku-mantle HEAD:master
```

Delete `service-account.json` from the workstation after the secret is set. Never commit it.

## Content Studio

Content Studio is an authenticated workspace for app-specific SEO articles and YouTube scripts.
It stores verified app facts, immutable briefs and drafts, overlap decisions, quality checks,
editorial images, and WordPress publication records.

1. Configure `CONTENT_SITEMAP_URL` and `CONTENT_ALLOWED_HOSTS`, then sync the inventory from the
   Content Studio index. The fetcher rejects off-domain redirects, non-HTML responses, and pages
   above `CONTENT_PAGE_MAX_BYTES`.
2. Set `OPENROUTER_API_KEY` to enable staged ideas, briefs, outlines, drafts, reviews, and image
   generation. Each stage requires structured JSON and is saved as an immutable version.
3. Use the existing private B2 settings for generated images. They live under content-addressed
   `content/` keys and are only copied to WordPress when a draft is created.
4. Configure all WordPress values together: `WORDPRESS_SITE_URL`, `WORDPRESS_USERNAME`, and
   `WORDPRESS_APPLICATION_PASSWORD`. Use a dedicated WordPress Application Password with only the
   permissions needed for the configured post type and media.
5. In Management, open Content Studio and configure each app's verified facts, approved claims,
   source URLs, languages, pillar page, listing URL, and related WordPress app ID.

The publication gate blocks WordPress until an accepted draft exists and overlap has been resolved.
Saving a WordPress draft and publishing it are separate actions. Publishing always requires a
browser confirmation; generation never publishes automatically.

## Scope semantics

No `?app=` means All apps. Lifecycle and financial figures add app installations, so one shop
installed in two apps counts twice. `?app=<slug>` narrows the complete page. Traffic, activation,
and annotation writes require one selected app.

## Operational settings

`POLL_INTERVAL_MINUTES` controls the sync cadence and the stale thresholds. Exactly one application
instance must run APScheduler. `SESSION_SECRET` must contain at least 32 characters off localhost.
`TRUSTED_CLIENT_IP_HEADER` must name a header your proxy overwrites. `SLACK_WEBHOOK_URL` and per-app
usage/GA4 integrations are optional.

`WATCHLIST_MEDIA_PATH` is the absolute directory used for archived public competitor icons and
screenshots. It defaults to `/data/mantle-watchlist`; production must mount durable storage there.
`WATCHLIST_CONCURRENCY` bounds the daily followed-listing collector to 1–4 workers and defaults to 2.
The sitemap still indexes the whole App Store daily and category pages still run twice weekly. Only
active followed apps receive the heavier daily listing and media fetch.
