alter table discovered_apps
    add column if not exists missing_scan_count integer not null default 0
        check (missing_scan_count >= 0),
    add column if not exists delisted_at timestamptz;

create index if not exists discovered_apps_delisted_idx
    on discovered_apps (delisted_at desc) where delisted_at is not null;

create table if not exists discovery_app_events (
    id bigserial primary key,
    discovered_app_id bigint not null references discovered_apps(id) on delete cascade,
    event_type text not null check (
        event_type in ('discovered', 'listing_updated', 'delisted', 'relisted')
    ),
    occurred_at timestamptz not null,
    previous_listing_updated_on date,
    listing_updated_on date,
    details jsonb not null default '{}'::jsonb,
    unique (discovered_app_id, event_type, occurred_at)
);

create index if not exists discovery_app_events_time_idx
    on discovery_app_events (occurred_at desc, event_type);
create index if not exists discovery_app_events_app_time_idx
    on discovery_app_events (discovered_app_id, occurred_at desc);

insert into discovery_app_events
    (discovered_app_id,event_type,occurred_at,listing_updated_on)
select id,'discovered',first_seen_at,listing_updated_on
from discovered_apps where not is_baseline
on conflict (discovered_app_id,event_type,occurred_at) do nothing;

alter table discovery_watchlist
    drop constraint if exists discovery_watchlist_follow_source_check;
alter table discovery_watchlist
    add constraint discovery_watchlist_follow_source_check check (
        follow_source in ('manual', 'rising_gem', 'new_contender', 'new_app')
    );

create table if not exists discovery_category_watchlist (
    category_id bigint primary key references discovery_categories(id) on delete cascade,
    active boolean not null default true,
    followed_at timestamptz not null,
    unfollowed_at timestamptz
);

create table if not exists discovery_alerts (
    id bigserial primary key,
    event_key text not null unique,
    alert_type text not null check (
        alert_type in ('new_category_app', 'listing_change', 'delisted')
    ),
    discovered_app_id bigint references discovered_apps(id) on delete cascade,
    category_id bigint references discovery_categories(id) on delete cascade,
    created_at timestamptz not null,
    payload jsonb not null default '{}'::jsonb,
    delivery_attempted_at timestamptz,
    delivered_at timestamptz,
    error_code text
);

create index if not exists discovery_alerts_delivery_idx
    on discovery_alerts (delivered_at, created_at) where delivered_at is null;
