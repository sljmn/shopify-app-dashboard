# Integrations and Runbook Design

## Goal

Add an authenticated management area to Mantle that is both the operational
source of truth for Partner organizations/apps and the durable runbook for
adding a new app. It replaces ad-hoc knowledge about Partner IDs, listing
locales, GA4 properties, and rollout state without putting plaintext secrets in
the database or UI.

## Navigation and views

The sidebar gains a `Beheer` entry with three views:

1. `Integraties` lists Partner organizations and their apps. It supports status
   filtering and shows app identity, listing locale/status, GA4 property,
   tracking state, credential presence, and the last update.
2. `App beheren` creates and edits organizations/apps. Records may be saved as
   drafts before every integration is ready. Activation is blocked until all
   required checks pass.
3. `Runbook` documents the exact human and agent workflow for creating a
   Partner account/app, configuring Dokku secrets, creating GA4 resources,
   connecting a Shopify listing, activating the catalog record, refreshing
   data, and validating the result.

The pages use the existing authenticated shell, visual tokens, responsive
table treatment, and mobile breakpoints. No public documentation route is
added.

## Data model

Partner organizations and apps move from runtime YAML ownership to PostgreSQL.
The database stores non-secret operational metadata:

- organization name and Partner organization ID;
- Partner token environment-variable name;
- app name, slug, Partner app GID, lifecycle status, and archive state;
- annual plan amounts;
- listing URL, primary locale, and listing workflow status/reason;
- GA4 property ID and credentials environment-variable name;
- Measurement Protocol tracking state;
- timestamps.

Lifecycle states are `draft`, `ready`, `active`, and `blocked`. Archiving is a
state transition, not deletion, so historical analytics remain addressable.

Secrets remain in Dokku environment variables. Mantle stores only the variable
name and reports `present`, `missing`, or `invalid`. It never renders or logs a
secret value.

## Migration and runtime ownership

`config/apps.yml` remains a bootstrap/import source for existing installations.
A migration imports its current organizations and apps into the database. After
bootstrap, database records drive request scoping, scheduler selection, and
manual refresh. Startup must not overwrite later CRUD changes from YAML.

The existing catalog value objects remain the boundary consumed by reporting,
sync, and scheduler code. A repository converts database rows plus current
environment variables into those objects, keeping secret resolution at runtime
and avoiding a broad rewrite of downstream services.

## Validation and activation

Draft records allow incomplete setup. Moving to `ready` or `active` validates:

- unique valid slug, Partner organization ID, and Partner app GID;
- a configured organization and present Partner token environment variable;
- GA4 property ID and present GA4 credentials environment variable;
- a valid primary listing locale;
- explicit listing and Measurement Protocol states;
- valid positive annual plan amounts.

An active app with a missing or invalid secret is excluded from scheduled sync
and shown as blocked in the management UI. Validation errors stay attached to
the submitted form.

## Runbook content

The runbook is written for both a human operator and a coding agent. It includes
copyable commands with placeholders, expected success signals, rollback steps,
and verification queries. It explicitly covers:

- creating or locating a Partner API client and setting its Dokku ENV variable;
- creating the GA4 property/web stream and granting the shared reader account;
- acknowledging user-data collection and creating one Measurement Protocol
  secret;
- using the listing's real primary locale (`nl` versus `en`) instead of guessing;
- handling `Submitted` and `In review` listings whose tracking fields are locked;
- adding the app as a draft, satisfying checks, activating it, refreshing data,
  and checking health/logs;
- rotating secrets without exposing them in commits, logs, screenshots, or UI.

## Error handling and security

All management mutations require the existing authenticated browser session and
CSRF protection. Unknown records return 404. Invalid transitions return the
form with actionable errors. Archive is confirmed and idempotent. Secret
presence checks never include values in HTML, application logs, exceptions, or
Markdown representations.

## Testing

Tests cover schema migration and YAML bootstrap, organization/app CRUD,
activation guards, archive behavior, database-to-catalog conversion, scheduler
selection, secret status masking, authentication/CSRF, runbook rendering, and
mobile-safe markup. Existing catalog and reporting tests remain green.
