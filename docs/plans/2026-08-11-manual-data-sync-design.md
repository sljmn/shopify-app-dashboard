# Manual Data Sync Design

## Goal

Add one dashboard control that can refresh the selected app or the full app
portfolio. It offers a normal incremental refresh and a complete historical
replay without blocking the browser request.

## User experience

The Overview health strip gains a **Fetch data** menu. Its two actions are:

- **Fetch fresh data** polls lifecycle events, transactions, current
  subscriptions/trials, and configured GA4 properties using their normal
  incremental windows.
- **Fetch all data again** replays lifecycle and transaction history, refreshes
  every current subscription, and reloads GA4 from its configured earliest
  date. This action requires confirmation because it is slower and makes many
  Partner API requests.

The app selector defines the scope. With **All apps** selected, every active app
is refreshed; with one app selected, only that app is refreshed. While a job is
running the health strip reports the current source and app, completed work,
and any errors. Only one manual job may run at a time. When it completes, the
Overview reloads so all figures come from the refreshed database state.

## Architecture

A process-local coordinator owns the single manual job. This matches the
existing deployment requirement of exactly one application process and avoids
introducing a queue or another service. The POST request validates
authentication and same-origin form submission, starts a daemon worker, and
returns immediately. A small JSON status route lets the page poll progress.

The coordinator calls the same pipeline functions as APScheduler. Those
functions gain explicit `full_history`/`force_full` arguments:

- lifecycle starts with no Partner cursor during a replay;
- transactions ignore the newest stored transaction during a replay;
- active subscriptions always query the complete currently installed set;
- GA4 starts at `GA4_EARLIEST_DATA` during a replay.

All storage remains idempotent. Historical replays upsert or deduplicate the
same platform identifiers and do not delete valid data. A failed source is
recorded and the job continues with the other sources and apps.

## Security and failure handling

Manual sync is a state-changing POST. It requires normal dashboard
authentication, an `application/x-www-form-urlencoded` body, and an Origin
matching `PUBLIC_BASE_URL` when the browser supplies one. This supports both
Google sessions and the local Basic-auth setup without exposing a cross-site
trigger.

Concurrent starts return HTTP 409. Invalid modes or app slugs return a clear
4xx response. Worker exceptions are reduced to short status messages and logged
with full tracebacks; tokens and Partner response bodies are never returned to
the browser. Process restarts may end an in-flight manual job, which is
acceptable because every source is safe to run again.

## Verification

Unit tests cover incremental versus full-history boundaries for lifecycle,
transactions, and GA4. Coordinator tests cover app scope, progress, partial
failure, and concurrent-start rejection. Web tests cover the POST security
boundary, selected-app scope, status JSON, and rendered controls. The complete
test suite and live database invariants must remain green, followed by desktop
and mobile browser checks against the local server.
