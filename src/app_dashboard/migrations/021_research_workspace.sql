create table if not exists research_lists (
    id bigserial primary key,
    title text not null check (btrim(title) <> ''),
    description text not null default '',
    status text not null default 'active' check (status in ('active', 'archived')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists research_lists_updated_idx
    on research_lists (updated_at desc, id desc);

create table if not exists research_list_apps (
    research_list_id bigint not null references research_lists(id) on delete cascade,
    discovered_app_id bigint not null references discovered_apps(id) on delete cascade,
    added_at timestamptz not null default now(),
    primary key (research_list_id, discovered_app_id)
);

create index if not exists research_list_apps_app_idx
    on research_list_apps (discovered_app_id, research_list_id);

create table if not exists discovered_developers (
    id bigserial primary key,
    name text not null check (btrim(name) <> ''),
    shopify_url text not null unique,
    last_scan_attempt_at timestamptz,
    last_scanned_at timestamptz,
    last_scan_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists discovered_developers_updated_idx
    on discovered_developers (updated_at desc, id desc);

create table if not exists discovered_app_developers (
    discovered_app_id bigint not null references discovered_apps(id) on delete cascade,
    discovered_developer_id bigint not null references discovered_developers(id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (discovered_app_id, discovered_developer_id)
);

create index if not exists discovered_app_developers_developer_idx
    on discovered_app_developers (discovered_developer_id, discovered_app_id);

create table if not exists research_notes (
    id bigserial primary key,
    title text not null check (btrim(title) <> ''),
    body text not null default '',
    research_list_id bigint references research_lists(id) on delete cascade,
    discovered_app_id bigint references discovered_apps(id) on delete cascade,
    discovered_developer_id bigint references discovered_developers(id) on delete cascade,
    author text not null check (btrim(author) <> ''),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint research_notes_exactly_one_target check (
        num_nonnulls(research_list_id, discovered_app_id, discovered_developer_id) = 1
    )
);

create index if not exists research_notes_updated_idx
    on research_notes (updated_at desc, id desc);
create index if not exists research_notes_list_idx
    on research_notes (research_list_id, updated_at desc) where research_list_id is not null;
create index if not exists research_notes_app_idx
    on research_notes (discovered_app_id, updated_at desc) where discovered_app_id is not null;
create index if not exists research_notes_developer_idx
    on research_notes (discovered_developer_id, updated_at desc)
    where discovered_developer_id is not null;

create table if not exists research_attachment_objects (
    digest text primary key check (digest ~ '^[0-9a-f]{64}$'),
    object_key text not null unique,
    mime_type text not null,
    byte_size bigint not null check (byte_size > 0),
    created_at timestamptz not null default now()
);

create table if not exists research_note_attachments (
    id bigserial primary key,
    research_note_id bigint not null references research_notes(id) on delete cascade,
    digest text not null references research_attachment_objects(digest),
    original_filename text not null check (btrim(original_filename) <> ''),
    position integer not null default 0 check (position >= 0),
    created_at timestamptz not null default now(),
    unique (research_note_id, digest),
    unique (research_note_id, position)
);

create index if not exists research_note_attachments_digest_idx
    on research_note_attachments (digest);
