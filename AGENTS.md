# Mantle Repository

This repository is the canonical source for Newcraft's Mantle application at
`https://mantle.newcraft.dev`.

## Stack and deployment

- The production application is Python 3.13 with FastAPI, Jinja, and Postgres.
- The Dokku application is `mantle` on `116.203.128.186`.
- Deploy production from this repository with `git push dokku-mantle master`.
- The legacy Ruby on Rails prototype is archived separately and is not the
  production Mantle application. Do not implement Mantle work in the Rails
  archive.

## Before changing code

- Read the relevant implementation and tests before answering questions about
  behavior.
- Run focused tests for the changed area, followed by the full test suite when
  shared reporting, ingestion, or application behavior changes.
- Never commit Partner API tokens, application credentials, merchant data, or
  production exports.

