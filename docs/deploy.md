# Deploy runbook

One container: the FastAPI dashboard plus an APScheduler poll loop, with Postgres for storage.
There is a `Dockerfile` and a `fly.toml`, but nothing in the application is Fly-specific.

Two constraints are real wherever you run it:

- **Exactly one instance.** Two means two APScheduler instances, so duplicate Partner API polls and
  duplicate Slack alerts. `fly.toml` pins `min_machines_running` and `max_machines_running` to 1.
- **Migrations on release.** `python -m app_dashboard.migrate` is idempotent and tracked in
  `schema_migrations`. It is wired as the `release_command`, and the container's `CMD` runs it again
  on boot as a second guard.

## Secrets

Every required setting has no default, so a missing one is a startup `ValidationError` rather than a
subtly wrong dashboard. Set them all before the first deploy:

```bash
fly secrets set \
  SHOPIFY_PARTNER_TOKEN_<org-id>=... \
  DASHBOARD_USERNAME=you@example.com \
  DASHBOARD_PASSWORD="$(python -c 'import secrets;print(secrets.token_urlsafe(24))')" \
  PUBLIC_BASE_URL=https://your-dashboard.example.com \
  SESSION_SECRET=$(python -c 'import secrets;print(secrets.token_urlsafe(32))') \
  TRUSTED_CLIENT_IP_HEADER=Fly-Client-IP
```

`DATABASE_URL` is set for you by `fly postgres attach`. `.env.example` is the complete list.
Repeat the Partner token argument for every `token_env` referenced by `config/apps.yml`.

Three of these decide whether the numbers are right rather than whether the app starts:

- **Each app's `annual_plan_amounts`** must list every annual price it charges, with cents. `AppSubscription`
  carries no billing-interval field, so an unlisted annual price is counted as monthly, at twelve
  times its true MRR. Empty means every plan is monthly, and the app logs a warning at startup.
  Setting it *after* charges are stored does not correct them: a corrected price only reaches a
  charge on re-ingest, so follow the change with a full replay (see below).
- **`TRUSTED_CLIENT_IP_HEADER`** must name the header your proxy sets. Wrong or unset behind a
  proxy, and rate limiting collapses every caller into one bucket.
- **`DASHBOARD_PASSWORD`** grants full dashboard access. Keep it in deployment secrets and rotate
  it when somebody should no longer be able to start a new session. Rotate `SESSION_SECRET` too
  when every existing session must be logged out immediately.

## One-time setup

```bash
fly launch --no-deploy        # picks up fly.toml; do not let it overwrite the app name
fly postgres create           # a separate Postgres app
fly postgres attach <postgres-app-name>
fly scale count 1             # confirm; see above
```

If you kept the repository's app name, deploy with `fly deploy -a <your-app>`, which overrides it.

## Deploy

```bash
fly deploy
```

Builds on remote builders, so no local Docker daemon is needed.

### Dokku watchlist media

Competitor listing JSON is stored in Postgres, but archived icons and screenshots need a persistent
host directory. Create and mount it before deploying migration 018:

```bash
mkdir -p /var/lib/dokku/data/storage/mantle-watchlist
chown -R 10001:10001 /var/lib/dokku/data/storage/mantle-watchlist
dokku storage:mount mantle /var/lib/dokku/data/storage/mantle-watchlist:/data/mantle-watchlist
dokku config:set --no-restart mantle \
  WATCHLIST_MEDIA_PATH=/data/mantle-watchlist WATCHLIST_CONCURRENCY=2
```

Keep one web process: the daily watchlist job is part of the same APScheduler instance. Back up the
mounted directory together with Postgres; the database contains content digests, not media bytes.

## Verify

Do not consider it deployed until all three pass:

1. `fly logs` shows at least one completed `run_sync` line. The first sync replays your app's full
   history and takes a few minutes.
2. `curl https://<your-host>/healthz` returns `{"status": "ok"}`.
3. `/customers` renders after signing in.

Then run the invariants against the live database. This is the check that catches a bad deploy the
health check cannot see:

```bash
DATABASE_URL='postgres://...' uv run python scripts/check_invariants.py
```

If Postgres is not directly reachable, tunnel to it first (`fly proxy 15432:5432 -a <db-app>`) and
point `DATABASE_URL` at the local end.

## After a change to derivation or a widened query

Use **Fetch data → Fetch all data again** for the selected app. A normal poll only asks for an
overlapping recent event window, so a change that widens the GraphQL query or corrects a stored
value needs history replayed. The full action omits the time boundary for that run; no cursor reset
or restart is needed.

Safe and repeatable: `raw_app_events` dedupes on its unique key, derivation is idempotent, and Slack
does not re-alert because replayed events keep their existing `app_events.id`.

## Backfilling country and industry

The Partner API exposes neither. If you have a CSV export from a vendor that did:

```bash
python -m app_dashboard.import_shops_csv shops <app-slug> path/to/export.csv
```

Retitle `COLUMN_MAP` in `src/app_dashboard/import_shops_csv.py` to match your export's header first. It is
update-only and never touches install state, so it is safe to re-run.

It deliberately maps no contact columns. See `migrations/008_drop_bad_contacts.sql` for why: those
columns list every staff account on the shop, which on an app installed by agencies means mostly
agencies, and a column headed "who to write to" that names somebody else's agency is worse than a
blank one.

## Marking a reviewer

Nothing in the Partner API reports reviews, so `shops.reviewed_at` is hand-maintained and the "Ask
for a review" call sheet is only ever as good as it is kept:

```sql
update shops set reviewed_at = '2026-01-15'
where app_id = (select id from apps where slug = 'example-app')
  and shop_domain = 'example.myshopify.com';
```
