create table if not exists discovery_watchlist (
    discovered_app_id bigint primary key references discovered_apps(id) on delete cascade,
    active boolean not null default true,
    follow_source text not null check (
        follow_source in ('manual', 'rising_gem', 'new_contender')
    ),
    followed_at timestamptz not null,
    unfollowed_at timestamptz,
    last_attempt_at timestamptz,
    last_success_at timestamptz,
    last_error_code text
);

create index if not exists discovery_watchlist_active_scan_idx
    on discovery_watchlist (active, last_success_at nulls first);

create table if not exists discovery_listing_snapshots (
    id bigserial primary key,
    discovered_app_id bigint not null references discovered_apps(id) on delete cascade,
    captured_at timestamptz not null,
    content_hash text not null,
    listing jsonb not null,
    unique (discovered_app_id, content_hash)
);

create index if not exists discovery_listing_snapshots_app_time_idx
    on discovery_listing_snapshots (discovered_app_id, captured_at desc, id desc);

create table if not exists discovery_listing_changes (
    id bigserial primary key,
    discovered_app_id bigint not null references discovered_apps(id) on delete cascade,
    snapshot_id bigint not null references discovery_listing_snapshots(id) on delete cascade,
    changed_at timestamptz not null,
    field text not null,
    before_value jsonb,
    after_value jsonb,
    unique (snapshot_id, field)
);

create index if not exists discovery_listing_changes_app_time_idx
    on discovery_listing_changes (discovered_app_id, changed_at desc, id desc);

create table if not exists discovery_media_objects (
    digest text primary key check (digest ~ '^[0-9a-f]{64}$'),
    object_key text not null unique,
    mime_type text not null,
    byte_size bigint not null check (byte_size > 0),
    width integer,
    height integer,
    created_at timestamptz not null
);

create table if not exists discovery_snapshot_media (
    snapshot_id bigint not null references discovery_listing_snapshots(id) on delete cascade,
    digest text not null references discovery_media_objects(digest),
    role text not null check (role in ('icon', 'screenshot')),
    position integer not null check (position >= 0),
    source_url text not null,
    primary key (snapshot_id, role, position)
);

create index if not exists discovery_snapshot_media_digest_idx
    on discovery_snapshot_media (digest);
