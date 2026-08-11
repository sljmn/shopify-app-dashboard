alter table organizations
    add column if not exists lifecycle_status text not null default 'active',
    add column if not exists archived_at timestamptz;

alter table apps
    add column if not exists lifecycle_status text not null default 'active',
    add column if not exists listing_status text not null default 'unknown',
    add column if not exists listing_status_reason text,
    add column if not exists tracking_status text not null default 'unknown',
    add column if not exists archived_at timestamptz;

alter table organizations
    add constraint organizations_lifecycle_status_check
    check (lifecycle_status in ('draft', 'ready', 'active', 'blocked'));

alter table apps
    add constraint apps_lifecycle_status_check
    check (lifecycle_status in ('draft', 'ready', 'active', 'blocked')),
    add constraint apps_listing_status_check
    check (listing_status in ('unknown', 'draft', 'submitted', 'in_review', 'published', 'blocked')),
    add constraint apps_tracking_status_check
    check (tracking_status in ('unknown', 'pending', 'connected', 'blocked'));

create index if not exists apps_management_status_idx
    on apps (lifecycle_status, archived_at, name);

-- Seed the operational state already verified during the August 2026 rollout.
update apps set listing_status='published'
where listing_url is not null;

update apps set tracking_status='connected'
where slug in (
    'eu-tax-exemption-easy', 'happy-birthday-marketing-app', 'delete-accounts',
    'kiezer-quiz-guided-selling', 'b2b-portal', 'cancel-direct', 'bol-sync',
    'onbuy-sync', 'image-translate-easy', 'eori-checker', 'isbn-book-importer',
    'ebay-reviews', 'tripadvisor-reviews', 'vinted-reviews', 'acumulus', 'billit'
);

update apps set listing_status='in_review', tracking_status='blocked',
    listing_status_reason='Shopify locks listing tracking fields during review.'
where slug='jortt';

update apps set listing_status='submitted', tracking_status='blocked',
    listing_status_reason='Shopify locks listing tracking fields while submitted.'
where slug in (
    'e-boekhouden', 'moneybird-sync', 'best-buy-reviews', 'bol-com-reviews',
    'booking-com-reviews', 'trustpilot-reviews', 'walmart-reviews',
    'yelp-reviews-importer'
);
