create table aso_rank_lists (
 id bigserial primary key, name text not null check (btrim(name)<>''), status text not null default 'active' check(status in ('active','archived')), created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table aso_rank_keywords (
 id bigserial primary key, rank_list_id bigint not null references aso_rank_lists(id) on delete cascade,
 keyword text not null check (btrim(keyword)<>''), locale text not null, country text not null default '', active boolean not null default true,
 last_scan_attempt_at timestamptz,last_scanned_at timestamptz,last_scan_error text,created_at timestamptz not null default now(),updated_at timestamptz not null default now(),
 unique(rank_list_id,keyword,locale,country)
);
create table aso_rank_scans (
 id bigserial primary key,rank_keyword_id bigint not null references aso_rank_keywords(id) on delete cascade,captured_at timestamptz not null,captured_on date not null,result_count integer not null check(result_count between 1 and 100),unique(rank_keyword_id,captured_on)
);
create table aso_rank_results (
 rank_scan_id bigint not null references aso_rank_scans(id) on delete cascade,discovered_app_id bigint not null references discovered_apps(id) on delete cascade,
 position integer not null check(position between 1 and 100),display_name text not null,review_count integer,rating numeric(3,2),built_for_shopify boolean not null default false,
 primary key(rank_scan_id,position),unique(rank_scan_id,discovered_app_id)
);
create index aso_rank_scans_keyword_date_idx on aso_rank_scans(rank_keyword_id,captured_on desc);
create index aso_rank_results_app_idx on aso_rank_results(discovered_app_id,rank_scan_id);
