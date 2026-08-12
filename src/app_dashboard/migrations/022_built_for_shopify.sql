alter table discovered_apps
    add column if not exists built_for_shopify boolean,
    add column if not exists bfs_checked_at timestamptz;

create index if not exists discovered_apps_bfs_idx
    on discovered_apps (built_for_shopify, bfs_checked_at desc);
