import os
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

# Settings has required fields, and app_dashboard.web builds the app at import time, so
# without these pytest fails during *collection* rather than in a test. Set
# before any app_dashboard module is imported: conftest runs first, which is the whole
# reason this block lives here and not in a fixture.
#
# These are deliberately assigned, not setdefault-ed. A developer with a real
# .env next to pyproject.toml would otherwise have their live Partner token and
# session secret read straight into the suite, because pydantic-settings lets
# the environment win over .env. DATABASE_URL is the one exception: point it
# wherever your test database actually is.
os.environ.setdefault("DATABASE_URL", "postgresql://localhost:5432/app_dashboard_test")
os.environ.update({
    "PARTNER_API_TOKEN": "test-token",
    "PARTNER_ORG_ID": "1",
    "PARTNER_APP_ID": "1",
    "DASHBOARD_USERNAME": "tester@example.com",
    "DASHBOARD_PASSWORD": "suite-only-credential",
    "PUBLIC_BASE_URL": "http://localhost:8000",
    "SESSION_SECRET": "test-session-secret-not-the-default",
    # The suite's fixture pricing: $19/month and $190/year. Tests that exercise
    # the inference itself override this, which is the point -- an annual price
    # the operator forgets to list is counted as monthly, at 12x its real MRR.
    "ANNUAL_PLAN_AMOUNTS": "190.00",
    # Never start APScheduler under test: two schedulers means duplicate polls.
    "NO_SCHEDULER": "1",
})

from app_dashboard.config import get_settings  # noqa: E402
from app_dashboard.catalog import AppConfig  # noqa: E402
from app_dashboard.db import connect, run_migrations  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    # Settings is lru_cached, so one test's monkeypatched env would otherwise
    # be read by the next one.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db():
    conn = connect()
    run_migrations(conn)
    tables = [
        row[0]
        for row in conn.execute(
            """
            select table_name from information_schema.tables
            where table_schema='public' and table_name != 'schema_migrations'
            """
        ).fetchall()
    ]
    if tables:
        conn.execute(f"truncate {', '.join(tables)} restart identity cascade")
    yield conn
    conn.close()


@pytest.fixture
def app_factory(db):
    sequence = 0

    def create(
        *,
        slug: str | None = None,
        name: str | None = None,
        annual_plan_amounts: frozenset[Decimal] = frozenset({Decimal("190.00")}),
    ) -> AppConfig:
        nonlocal sequence
        sequence += 1
        slug = slug or f"app-{sequence}"
        name = name or f"App {sequence}"
        partner_org_id = f"test-org-{sequence}"
        organization_id = db.execute(
            """
            insert into organizations (partner_org_id, name, token_env)
            values (%s, %s, %s) returning id
            """,
            (partner_org_id, f"Organization {sequence}", f"TOKEN_{sequence}"),
        ).fetchone()[0]
        partner_app_id = f"gid://partners/App/{sequence}"
        app_id = db.execute(
            """
            insert into apps (
                organization_id, partner_app_id, slug, name, annual_plan_amounts
            ) values (%s, %s, %s, %s, %s) returning id
            """,
            (
                organization_id,
                partner_app_id,
                slug,
                name,
                Jsonb([str(value) for value in sorted(annual_plan_amounts)]),
            ),
        ).fetchone()[0]
        return AppConfig(
            id=app_id,
            organization_id=organization_id,
            slug=slug,
            name=name,
            partner_app_id=partner_app_id,
            partner_org_id=partner_org_id,
            organization_name=f"Organization {sequence}",
            partner_token_env=f"TOKEN_{sequence}",
            partner_token=f"token-{sequence}",
            annual_plan_amounts=annual_plan_amounts,
            listing_url=None,
            usage_token_env=None,
            usage_token=None,
            usage_event_types=frozenset(),
            usage_activation_event=None,
            usage_live_event=None,
            ga4_property_id=None,
            ga4_credentials_env=None,
            ga4_credentials_json=None,
        )

    return create


@pytest.fixture
def test_app(app_factory):
    return app_factory(slug="test-app", name="Test App")
