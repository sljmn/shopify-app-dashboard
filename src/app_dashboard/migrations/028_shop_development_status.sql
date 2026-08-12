-- Shopify's native Reviews API displays a test-only modal for Partner
-- development stores, but those stores cannot submit public reviews. Apps
-- report the authoritative Admin GraphQL Shop.plan.partnerDevelopment value.
alter table shops
    add column if not exists partner_development boolean;

create index if not exists shops_partner_development_idx
    on shops (app_id, partner_development)
    where partner_development is true;
