-- Current Shopify subscription state. The lifecycle event feed carries prices,
-- but only activeSubscription exposes whether a shop is still in trial or has
-- scheduled cancellation at the end of its current cycle.
create table active_subscriptions (
    app_id bigint not null,
    shop_gid text not null,
    legacy_subscription_id text,
    billing_period text,
    trial_ends_at timestamptz,
    cancel_at_end_of_cycle boolean not null default false,
    item_handle text,
    item_description text,
    currency_code text,
    payload jsonb not null default '{}'::jsonb,
    observed_at timestamptz not null,
    primary key (app_id, shop_gid),
    foreign key (app_id, shop_gid)
        references shops(app_id, shop_gid) on delete cascade
);

create index active_subscriptions_app_trial_idx
    on active_subscriptions (app_id, trial_ends_at);
create index active_subscriptions_app_legacy_idx
    on active_subscriptions (app_id, legacy_subscription_id);
