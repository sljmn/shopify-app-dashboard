create table if not exists discovery_app_observations (
    discovered_app_id bigint not null references discovered_apps(id) on delete cascade,
    observed_on date not null,
    review_count integer check (review_count >= 0),
    rating numeric(3, 2) check (rating between 0 and 5),
    best_category_rank integer not null check (best_category_rank > 0),
    observed_at timestamptz not null,
    primary key (discovered_app_id, observed_on)
);

create index if not exists discovery_app_observations_date_idx
    on discovery_app_observations (observed_on desc, review_count desc);

create table if not exists discovery_category_observations (
    discovered_app_id bigint not null references discovered_apps(id) on delete cascade,
    category_id bigint not null references discovery_categories(id) on delete cascade,
    observed_on date not null,
    position integer not null check (position > 0),
    observed_at timestamptz not null,
    primary key (discovered_app_id, category_id, observed_on)
);

create index if not exists discovery_category_observations_rank_idx
    on discovery_category_observations (category_id, observed_on desc, position);
