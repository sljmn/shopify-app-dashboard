create table if not exists content_style_profiles (
    id bigserial primary key,
    name text not null unique,
    version integer not null default 1 check (version > 0),
    prompt_template text not null,
    palette text not null default '',
    rules jsonb not null default '{}'::jsonb,
    reference_objects jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    unique (name, version)
);

create table if not exists app_content_profiles (
    app_id bigint primary key references apps(id) on delete cascade,
    pillar_url text,
    shopify_listing_url text,
    wordpress_related_app_id bigint,
    default_language text not null default 'en' check (default_language ~ '^[a-z]{2}(-[A-Z]{2})?$'),
    supported_languages jsonb not null default '["en"]'::jsonb,
    facts jsonb not null default '[]'::jsonb,
    allowed_claims jsonb not null default '[]'::jsonb,
    forbidden_claims jsonb not null default '[]'::jsonb,
    audiences jsonb not null default '[]'::jsonb,
    objections jsonb not null default '[]'::jsonb,
    source_urls jsonb not null default '[]'::jsonb,
    style_profile_id bigint references content_style_profiles(id) on delete set null,
    updated_at timestamptz not null default now()
);

create table if not exists content_inventory (
    id bigserial primary key,
    canonical_url text not null unique,
    wordpress_id bigint,
    title text not null,
    slug text not null,
    language text not null default 'en',
    headings jsonb not null default '[]'::jsonb,
    summary text not null default '',
    body_text text not null default '',
    content_digest text not null,
    published_at timestamptz,
    modified_at timestamptz,
    linked_app_id bigint references apps(id) on delete set null,
    last_seen_at timestamptz not null default now(),
    removed_at timestamptz
);
create index if not exists content_inventory_seen_idx on content_inventory (last_seen_at desc);

create table if not exists content_projects (
    id bigserial primary key,
    app_id bigint not null references apps(id) on delete cascade,
    title text not null,
    target_query text not null,
    channel text not null check (channel in ('seo_article','youtube')),
    language text not null check (language ~ '^[a-z]{2}(-[A-Z]{2})?$'),
    intent text not null default '',
    stage text not null default 'idea' check (stage in ('idea','brief','outline','draft','review','media','ready','published','archived')),
    overlap_status text not null default 'unchecked' check (overlap_status in ('unchecked','clear','differentiate','update_existing','blocked')),
    overlap_note text not null default '',
    update_inventory_id bigint references content_inventory(id) on delete set null,
    author text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists content_projects_filters_idx on content_projects (stage, channel, language, updated_at desc);

create table if not exists content_versions (
    id bigserial primary key,
    project_id bigint not null references content_projects(id) on delete cascade,
    stage text not null check (stage in ('idea','brief','outline','draft','review','media','ready','published','archived')),
    version_number integer not null check (version_number > 0),
    payload jsonb not null,
    rendered_text text not null default '',
    model text,
    policy_version text not null,
    author text not null,
    accepted boolean not null default false,
    created_at timestamptz not null default now(),
    unique (project_id, stage, version_number)
);
create unique index if not exists content_versions_one_accepted_idx on content_versions (project_id, stage) where accepted;

create table if not exists content_sources (
    id bigserial primary key,
    project_id bigint not null references content_projects(id) on delete cascade,
    url text not null,
    title text not null default '',
    excerpt text not null default '',
    digest text not null,
    selected boolean not null default true,
    captured_at timestamptz not null default now(),
    unique (project_id, digest)
);

create table if not exists content_links (
    id bigserial primary key,
    project_id bigint not null references content_projects(id) on delete cascade,
    url text not null,
    anchor_text text not null,
    role text not null check (role in ('pillar','listing','internal','external')),
    validation_status text not null default 'unchecked' check (validation_status in ('unchecked','valid','invalid')),
    unique (project_id, url, anchor_text)
);

create table if not exists content_media (
    id bigserial primary key,
    project_id bigint not null references content_projects(id) on delete cascade,
    role text not null check (role in ('featured','inline','youtube_thumbnail')),
    digest text not null,
    object_key text not null,
    mime_type text not null,
    byte_size bigint not null check (byte_size > 0),
    width integer,
    height integer,
    alt_text text not null default '',
    prompt text not null default '',
    model text,
    style_profile_id bigint references content_style_profiles(id) on delete set null,
    selected boolean not null default false,
    wordpress_media_id bigint,
    created_at timestamptz not null default now(),
    unique (project_id, digest)
);
create unique index if not exists content_media_one_selected_idx on content_media (project_id, role) where selected;

create table if not exists content_quality_checks (
    id bigserial primary key,
    project_id bigint not null references content_projects(id) on delete cascade,
    version_id bigint references content_versions(id) on delete cascade,
    check_name text not null,
    severity text not null check (severity in ('pass','warning','block')),
    evidence text not null default '',
    created_at timestamptz not null default now()
);

create table if not exists content_runs (
    id bigserial primary key,
    project_id bigint references content_projects(id) on delete cascade,
    run_type text not null,
    status text not null check (status in ('running','succeeded','failed')),
    model text,
    request_id text,
    usage jsonb not null default '{}'::jsonb,
    safe_error text,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);
create index if not exists content_runs_project_idx on content_runs (project_id, started_at desc);

create table if not exists content_publications (
    id bigserial primary key,
    project_id bigint not null references content_projects(id) on delete cascade,
    wordpress_post_id bigint,
    wordpress_url text,
    status text not null check (status in ('draft','future','publish','failed','archived')),
    payload_hash text not null,
    response jsonb not null default '{}'::jsonb,
    safe_error text,
    published_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create unique index if not exists content_publications_active_idx on content_publications (project_id) where status in ('draft','future','publish');

insert into content_style_profiles (name, prompt_template, palette, rules)
values (
    'Newcraft editorial',
    'Create a warm, textured editorial illustration about {subject}. No words, logos, UI text, gradients, or generic stock imagery.',
    'deep green, ochre, rust, cream, navy',
    '{"composition":"clear editorial focal point","avoid":["text","logos","purple gradients","stock photography"]}'::jsonb
) on conflict (name) do nothing;
