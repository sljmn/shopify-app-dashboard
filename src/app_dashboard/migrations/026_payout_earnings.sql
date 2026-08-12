-- Shopify's historical Earning events are the only Partner API source that
-- carries a settlement date. Keep them apart from transactions: created_at on
-- a transaction is when money was recorded, not when Shopify settled it.
create table payout_earnings (
    app_id bigint not null references apps(id),
    id text not null,
    event_type text not null,
    earning_type text not null,
    occurred_at timestamptz not null,
    settlement_date date,
    shop_gid text,
    description text,
    gross_amount numeric(12,2),
    shopify_fee numeric(12,2),
    net_amount numeric(12,2) not null,
    currency_code text not null,
    ingested_at timestamptz not null default now(),
    primary key (app_id, id)
);

create index payout_earnings_app_settlement_idx
    on payout_earnings (app_id, settlement_date desc);
create index payout_earnings_occurred_idx
    on payout_earnings (occurred_at desc);
