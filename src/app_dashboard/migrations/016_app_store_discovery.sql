create table if not exists discovered_apps (
    id bigserial primary key,
    handle text not null unique,
    display_name text,
    listing_updated_on date,
    first_seen_at timestamptz not null,
    last_seen_at timestamptz not null,
    is_baseline boolean not null default false
);

create index if not exists discovered_apps_first_seen_idx
    on discovered_apps (first_seen_at desc);
create index if not exists discovered_apps_listing_updated_idx
    on discovered_apps (listing_updated_on desc);

create table if not exists discovery_categories (
    id bigserial primary key,
    slug text not null unique,
    name text not null,
    app_count integer not null default 0 check (app_count >= 0),
    observed_at timestamptz not null
);

create table if not exists discovered_app_categories (
    discovered_app_id bigint not null references discovered_apps(id) on delete cascade,
    category_id bigint not null references discovery_categories(id) on delete cascade,
    primary key (discovered_app_id, category_id)
);

create index if not exists discovered_app_categories_category_idx
    on discovered_app_categories (category_id, discovered_app_id);

create table if not exists discovery_state (
    source text primary key,
    baseline_completed_at timestamptz,
    last_success_at timestamptz not null
);
