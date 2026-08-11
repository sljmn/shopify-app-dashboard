# Empty ASO Keywords Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop blank GA4 search terms from appearing as valid ASO keyword intelligence.

**Architecture:** Enforce the non-empty keyword boundary both when importing and when reporting. Persist an explicit unsupported capability when GA4 returns no usable terms, and render a truthful empty state instead of a blank keyword row.

**Tech Stack:** Python 3.13, FastAPI, PostgreSQL, Jinja2, pytest

---

### Task 1: Lock down the data boundary

**Files:**
- Modify: `tests/test_aso_ga4.py`
- Modify: `tests/test_aso.py`
- Modify: `src/app_dashboard/aso_ga4.py`
- Modify: `src/app_dashboard/aso.py`

- [ ] Add a failing importer test proving empty and whitespace-only terms are discarded.
- [ ] Add a failing report test proving previously stored blank rows are ignored.
- [ ] Run the focused tests and confirm they fail for the blank keyword behavior.
- [ ] Filter normalized blank terms in the importer and add a defensive SQL predicate in reports.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Expose an honest source state

**Files:**
- Modify: `tests/test_aso_ga4.py`
- Modify: `tests/test_web.py`
- Modify: `src/app_dashboard/aso_ga4.py`
- Modify: `src/app_dashboard/templates/aso.html`
- Create: `src/app_dashboard/migrations/015_remove_empty_aso_keywords.sql`

- [ ] Add failing tests for `NoKeywordValues` capability state and the explanatory UI empty state.
- [ ] Run the focused tests and confirm the current implementation fails.
- [ ] Store `unsupported` after an import with no usable terms and render the matching message.
- [ ] Delete previously persisted blank keyword rows in a migration.
- [ ] Run the ASO, web, migration, and full test suites.
- [ ] Refresh production and verify the app shows zero observed keywords without a blank row.
