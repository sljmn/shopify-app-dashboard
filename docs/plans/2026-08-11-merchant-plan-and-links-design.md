# Merchant Plan And Links Design

## Objective

Make merchant billing status unambiguous and make shops reachable from the
Overview activity feed without removing the existing merchant detail workflow.

## Chosen Design

On merchant detail, an active subscription with zero monthly value renders as
`Free plan` with `No recurring charge`. Positive monthly and annual
subscriptions keep their existing amount-based presentation. This uses the
normalized subscription amount already used for MRR, so no schema migration or
Partner-data fallback is needed.

In Latest activity, the shop name links to the scoped merchant detail page. If
the shop has a domain, a separate `storefront` link opens that domain. The
internal link preserves `?app=<slug>` when an app is selected, while the
storefront remains a direct external URL.

## Data Flow

`recent_events` returns the stable shop GID and current shop domain alongside
the existing display name. The Overview template uses the GID for the internal
route and the domain for the external link. Jinja URL encoding and escaping
remain in force.

## Verification

- A zero-value active subscription renders `Free plan`, not `$0 /mo`.
- A positive subscription retains the existing paid amount display.
- Recent activity shop names link to merchant detail with the selected app.
- Rows with a domain also show a direct HTTPS storefront link.
- The focused tests, full suite, and a local browser smoke test pass.

