alter table discovered_apps
    add column if not exists icon_url text,
    add column if not exists icon_digest text references discovery_media_objects(digest),
    add column if not exists icon_archived_url text,
    add column if not exists icon_checked_at timestamptz,
    add column if not exists icon_error_code text;

create index if not exists discovered_apps_icon_backfill_idx
    on discovered_apps (icon_checked_at nulls first, first_seen_at desc)
    where delisted_at is null and icon_url is not null;

update discovered_apps app set icon_url=latest.icon_url
from (
    select distinct on (discovered_app_id) discovered_app_id,listing->>'icon' icon_url
    from discovery_listing_snapshots
    where coalesce(listing->>'icon','')<>''
    order by discovered_app_id,captured_at desc,id desc
) latest
where latest.discovered_app_id=app.id and app.icon_url is null;
