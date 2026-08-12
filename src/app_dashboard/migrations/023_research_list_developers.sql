create table if not exists research_list_developers (
    research_list_id bigint not null references research_lists(id) on delete cascade,
    discovered_developer_id bigint not null references discovered_developers(id) on delete cascade,
    added_at timestamptz not null default now(),
    primary key (research_list_id, discovered_developer_id)
);

create index if not exists research_list_developers_developer_idx
    on research_list_developers (discovered_developer_id, research_list_id);
