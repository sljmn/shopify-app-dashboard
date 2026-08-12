# Payout cashflow overview

## Goal

Make Payouts match the useful Mantle presentation: show recent and upcoming Shopify payout periods, their amounts and expected payment dates at a glance, while preserving the existing earning-level ledger as the source-of-truth drill-down.

## Data semantics

The page separates authoritative Shopify data from projections.

- **Paid** uses earnings with a Shopify `settlementDate` in a completed payout period.
- **Due** uses earnings assigned to the current payout period whose settlement date is known but whose expected payment date is still ahead.
- **Billed** uses recorded earnings without a settlement date, grouped into Shopify's payout windows from `occurredAt`.
- **Upcoming (Estimated)** projects the next payout window from current recurring subscription value. Estimated money is always labelled and is never included in settled totals.

Shopify's twice-monthly Partner payout schedule and business-day delay determine period labels and expected payment dates. Existing annual-plan normalization remains the source for projected recurring value.

## Interface

The top of `/payouts` becomes a responsive cashflow panel containing:

- a stacked bar chart for the latest paid period, current period and next period;
- a compact legend for Paid, Due, Billed and Upcoming;
- one summary row per payout period with period range, payment message, amount and an Estimated marker where applicable;
- a `View details` action that moves to the existing settlement history.

The date filter and ledger stay available below the overview. On mobile the chart, legend and period rows use the full available width and do not require horizontal scrolling.

## Reliability

Amounts from `payout_earnings` remain exact and currency-specific. Projections never merge currencies or present an estimated bank-arrival status as confirmed. Empty periods and mixed currencies render explicitly. Tests cover payout-window assignment, business-day payment dates, status classification, projection labelling and responsive page structure.
