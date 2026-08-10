-- Multi-app is a new ownership model, not a label added to ambiguous rows.
-- Refuse an in-place upgrade when any app-owned data exists: there is no
-- trustworthy way to infer which app owns a historical row.
do $$
begin
    if exists (select 1 from raw_app_events limit 1)
       or exists (select 1 from app_events limit 1)
       or exists (select 1 from charges limit 1)
       or exists (select 1 from subscriptions limit 1)
       or exists (select 1 from shops limit 1)
       or exists (select 1 from transactions limit 1)
       or exists (select 1 from sync_state limit 1)
       or exists (select 1 from usage_events limit 1)
       or exists (select 1 from ga4_daily limit 1)
       or exists (select 1 from annotations limit 1)
       or exists (select 1 from tracking_events limit 1)
    then
        raise exception using message =
            'Multi-app migration requires a new empty database. Configure the catalog, migrate a fresh database, then replay every app from its source.';
    end if;
end $$;

create table organizations (
    id bigserial primary key,
    partner_org_id text not null unique,
    name text not null,
    token_env text not null,
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table apps (
    id bigserial primary key,
    organization_id bigint not null references organizations(id),
    partner_app_id text not null unique,
    slug text not null unique,
    name text not null,
    listing_url text,
    annual_plan_amounts jsonb not null default '[]'::jsonb,
    usage_token_env text,
    usage_event_types jsonb not null default '[]'::jsonb,
    usage_activation_event text,
    usage_live_event text,
    ga4_property_id text,
    ga4_credentials_env text,
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table raw_app_events add column app_id bigint not null references apps(id);
alter table app_events add column app_id bigint not null references apps(id);
alter table charges add column app_id bigint not null references apps(id);
alter table subscriptions add column app_id bigint not null references apps(id);
alter table shops add column app_id bigint not null references apps(id);
alter table transactions add column app_id bigint not null references apps(id);
alter table sync_state add column app_id bigint not null references apps(id);
alter table usage_events add column app_id bigint not null references apps(id);
alter table ga4_daily add column app_id bigint not null references apps(id);
alter table annotations add column app_id bigint not null references apps(id);
alter table tracking_events add column app_id bigint not null references apps(id);

alter table raw_app_events drop constraint raw_app_events_pkey;
alter table raw_app_events
    drop constraint raw_app_events_app_installation_id_type_occurred_at_coalesc_key;
alter table raw_app_events add primary key (app_id, id);
alter table raw_app_events add unique (
    app_id, shop_gid, type, occurred_at, coalesce_charge
);

alter table charges drop constraint charges_pkey;
alter table charges add primary key (app_id, gid);

alter table app_events drop constraint app_events_platform_event_id_key;
alter table app_events add unique (app_id, platform_event_id);

alter table subscriptions drop constraint subscriptions_pkey;
alter table subscriptions add primary key (app_id, id);

alter table shops drop constraint shops_pkey;
alter table shops add primary key (app_id, shop_gid);

alter table transactions drop constraint transactions_pkey;
alter table transactions add primary key (app_id, id);

alter table sync_state drop constraint sync_state_pkey;
alter table sync_state add primary key (app_id, source);

alter table usage_events drop constraint usage_events_pkey;
alter table usage_events add primary key (app_id, shop_gid, event_id);

alter table ga4_daily drop constraint ga4_daily_pkey;
alter table ga4_daily add primary key (app_id, date, dimension, value);

create table operations_state (
    source text primary key,
    last_run_at timestamptz
);

create index raw_app_events_app_ingested_idx
    on raw_app_events (app_id, ingested_at);
create index app_events_app_type_occurred_idx
    on app_events (app_id, type, occurred_at);
create index app_events_app_shop_idx on app_events (app_id, shop_gid);
create index charges_app_subscription_idx on charges (app_id, subscription_id);
create index subscriptions_app_shop_idx on subscriptions (app_id, shop_gid);
create index shops_app_state_idx on shops (app_id, install_state);
create index transactions_app_created_idx on transactions (app_id, created_at);
create index usage_events_app_shop_type_time_idx
    on usage_events (app_id, shop_gid, event_type, occurred_at);
create index ga4_daily_app_dimension_date_idx
    on ga4_daily (app_id, dimension, date);
create index annotations_app_date_idx on annotations (app_id, on_date desc);
create index tracking_events_app_time_idx on tracking_events (app_id, occurred_at);
