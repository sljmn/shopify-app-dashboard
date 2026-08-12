# Payout settlements

## Goal

Show when Shopify settled app earnings and how much net money was included, without treating transaction dates or MRR as payout dates.

## Data source

Use the Partner API historical `events` query and its `Earning` object. `settlementDate` is the settlement date and `netAmount` is the amount the partner receives after Shopify commission. This feed is separate from both `app.events` (subscription lifecycle) and `transactions` (money movements recorded at `createdAt`).

Mantle stores every earning idempotently by `(app_id, earning_id)`. A repeated sync updates settlement and amount fields because Shopify can expose an earning before its settlement date is known. The initial window begins at the first transaction Mantle already holds for that app; subsequent syncs overlap the latest earning timestamp.

## Interface

Add a signed-in `/payouts` page and sidebar item. The page defaults to the last twelve months and supports a from/to date filter. Its main table groups settled earnings by settlement date and currency. Selecting a date shows the underlying app, shop, earning type and net amount rows.

The copy explicitly calls these Shopify settlement dates. It does not claim to expose bank-transfer status, payout references, or arrival dates because the Partner API does not provide those fields here.

## Failure and verification

Payout sync has its own source and scheduler job, so a failure cannot stop lifecycle, transaction, or subscription syncs. Automated coverage verifies GraphQL mapping, idempotent storage, grouped reporting, app scoping, authentication, and rendered date/amount output.
