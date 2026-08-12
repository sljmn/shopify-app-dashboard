alter table apps
    add column if not exists review_prompt_enabled boolean not null default false,
    add column if not exists review_trigger_event text,
    add column if not exists review_min_success_count integer not null default 1
        check (review_min_success_count > 0),
    add column if not exists review_min_install_hours integer not null default 24
        check (review_min_install_hours >= 24),
    add column if not exists review_retry_days integer not null default 90
        check (review_retry_days > 0),
    add column if not exists review_annual_cap integer not null default 3
        check (review_annual_cap between 1 and 3);

create table if not exists merchant_contacts (
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
    updated_at timestamptz not null default now(),
    check ((kind = 'shop' and shopify_user_id is null)
        or (kind = 'staff' and shopify_user_id is not null)),
    check (last_seen_at >= first_seen_at)
);

create unique index if not exists merchant_contacts_shop_unique
    on merchant_contacts (app_id, shop_gid) where kind = 'shop';
create unique index if not exists merchant_contacts_staff_unique
    on merchant_contacts (app_id, shop_gid, shopify_user_id) where kind = 'staff';
create index if not exists merchant_contacts_shop_seen_idx
    on merchant_contacts (app_id, shop_gid, last_seen_at desc);

create table if not exists review_prompt_decisions (
    id bigserial primary key,
    decision_id text not null unique,
    app_id bigint not null references apps(id) on delete cascade,
    shop_gid text not null,
    event_id text not null,
    event_type text not null,
    issued_at timestamptz not null,
    expires_at timestamptz not null,
    outcome text not null default 'issued' check (outcome in (
        'issued', 'shown', 'temporarily_declined', 'cancelled',
        'already_reviewed', 'ineligible', 'failed', 'expired'
    )),
    response_code text,
    response_message text,
    responded_at timestamptz,
    next_eligible_at timestamptz,
    created_at timestamptz not null default now(),
    unique (app_id, shop_gid, event_id),
    check (expires_at > issued_at)
);

create index if not exists review_prompt_decisions_shop_idx
    on review_prompt_decisions (app_id, shop_gid, issued_at desc);
create index if not exists review_prompt_decisions_next_idx
    on review_prompt_decisions (app_id, next_eligible_at)
    where next_eligible_at is not null;

create table if not exists review_prompt_suppressions (
    app_id bigint not null references apps(id) on delete cascade,
    shop_gid text not null,
    reason text not null,
    suppressed_at timestamptz not null default now(),
    suppressed_by text not null,
    primary key (app_id, shop_gid)
);
