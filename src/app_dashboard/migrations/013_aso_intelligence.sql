alter table apps
    add column if not exists listing_locales jsonb not null default '["en"]'::jsonb;

create table if not exists aso_source_capabilities (
    app_id bigint not null references apps(id) on delete cascade,
    source text not null,
    status text not null check (status in ('ready', 'partial', 'unsupported', 'failed')),
    fields jsonb not null default '{}'::jsonb,
    checked_at timestamptz not null,
    error_code text,
    primary key (app_id, source)
);

create table if not exists aso_keyword_daily (
    app_id bigint not null references apps(id) on delete cascade,
    date date not null,
    keyword text not null,
    locale text not null default '',
    country text not null default '',
    device text not null default 'unknown',
    search_type text not null,
    users integer not null default 0 check (users >= 0),
    install_clicks integer not null default 0 check (install_clicks >= 0),
    average_position numeric(8, 2),
    latest_position integer,
    position_samples integer not null default 0 check (position_samples >= 0),
    primary key (app_id, date, keyword, locale, country, device, search_type)
);

create index if not exists aso_keyword_app_date_idx
    on aso_keyword_daily (app_id, date desc);
create index if not exists aso_keyword_app_term_idx
    on aso_keyword_daily (app_id, keyword);

create table if not exists aso_install_sources (
    app_id bigint not null references apps(id) on delete cascade,
    attribution_key text not null,
    shop_domain text not null,
    shop_id text,
    installed_on date not null,
    source text not null,
    source_type text not null default '',
    source_value text not null default '',
    locale text not null default '',
    country text not null default '',
    device text not null default 'unknown',
    observed_at timestamptz not null default now(),
    primary key (app_id, attribution_key)
);

create index if not exists aso_install_app_shop_idx
    on aso_install_sources (app_id, shop_domain);
create index if not exists aso_install_app_date_idx
    on aso_install_sources (app_id, installed_on desc);

create table if not exists aso_listing_snapshots (
    id bigserial primary key,
    app_id bigint not null references apps(id) on delete cascade,
    locale text not null,
    captured_at timestamptz not null,
    content_hash text not null,
    listing jsonb not null,
    unique (app_id, locale, content_hash)
);

create index if not exists aso_listing_app_locale_time_idx
    on aso_listing_snapshots (app_id, locale, captured_at desc);

create table if not exists aso_listing_changes (
    id bigserial primary key,
    app_id bigint not null references apps(id) on delete cascade,
    snapshot_id bigint not null references aso_listing_snapshots(id) on delete cascade,
    locale text not null,
    changed_at timestamptz not null,
    field text not null,
    before_value jsonb,
    after_value jsonb,
    unique (snapshot_id, field)
);

create table if not exists aso_popular_keywords (
    keyword text primary key,
    source text not null check (source in ('autocomplete', 'manual')),
    first_seen_at timestamptz not null,
    last_seen_at timestamptz not null
);
