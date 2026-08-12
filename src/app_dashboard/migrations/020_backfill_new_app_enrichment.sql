insert into discovery_watchlist
    (discovered_app_id,active,follow_source,followed_at)
select app.id,true,'new_app',event.occurred_at
from discovery_app_events event
join discovered_apps app on app.id=event.discovered_app_id
where event.event_type='discovered'
  and not exists (
    select 1 from discovery_watchlist watch
    where watch.discovered_app_id=app.id
  )
on conflict (discovered_app_id) do nothing;
