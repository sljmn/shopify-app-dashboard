create table if not exists discovery_reviews (
    id bigserial primary key,
    discovered_app_id bigint not null references discovered_apps(id) on delete cascade,
    shopify_review_id bigint not null,
    rating smallint not null check (rating between 1 and 5),
    reviewed_on date not null,
    merchant_name text,
    country text,
    usage_duration text,
    body text not null,
    developer_reply text,
    developer_replied_on date,
    source_url text not null,
    first_captured_at timestamptz not null,
    last_captured_at timestamptz not null,
    unique (discovered_app_id, shopify_review_id)
);

create index if not exists discovery_reviews_app_date_idx
    on discovery_reviews (discovered_app_id, reviewed_on desc, shopify_review_id desc);
create index if not exists discovery_reviews_app_rating_idx
    on discovery_reviews (discovered_app_id, rating, reviewed_on desc);

create table if not exists discovery_review_sync_state (
    discovered_app_id bigint primary key references discovered_apps(id) on delete cascade,
    next_backfill_page integer not null default 1 check (next_backfill_page >= 1),
    backfill_completed_at timestamptz,
    last_attempt_at timestamptz,
    last_success_at timestamptz,
    last_error_code text
);
