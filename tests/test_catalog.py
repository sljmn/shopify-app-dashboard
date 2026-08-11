from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app_dashboard.catalog import (
    CatalogError,
    app_by_slug,
    load_catalog,
    reconcile_catalog,
)


TOKEN_ENV = {
    "SHOPIFY_PARTNER_TOKEN_3508770": "token-3508770",
    "SHOPIFY_PARTNER_TOKEN_4626496": "token-4626496",
    "SHOPIFY_PARTNER_TOKEN_4653231": "token-4653231",
    "SHOPIFY_PARTNER_TOKEN_4742901": "token-4742901",
    "SHOPIFY_PARTNER_TOKEN_4821379": "token-4821379",
    "SHOPIFY_PARTNER_TOKEN_4975891": "token-4975891",
}


def test_repository_catalog_contains_all_configured_apps():
    path = Path(__file__).parents[1] / "config/apps.yml"
    apps = load_catalog(path, TOKEN_ENV)

    assert len(apps) == 25
    assert len({app.slug for app in apps}) == 25
    assert len({app.partner_app_id for app in apps}) == 25
    tax = next(app for app in apps if app.slug == "eu-tax-exemption-easy")
    assert tax.partner_org_id == "3508770"
    assert tax.annual_plan_amounts == {
        Decimal("99.90"), Decimal("249.90"), Decimal("499.00")
    }
    b2b = next(app for app in apps if app.slug == "b2b-portal")
    assert b2b.annual_plan_amounts == {Decimal("150.00")}
    isbn = next(app for app in apps if app.slug == "isbn-book-importer")
    assert isbn.annual_plan_amounts == {
        Decimal("100.00"), Decimal("190.00"), Decimal("390.00")
    }
    review_apps = [app for app in apps if app.partner_org_id == "4975891"]
    assert {app.slug for app in review_apps} == {
        "best-buy-reviews",
        "bol-com-reviews",
        "booking-com-reviews",
        "ebay-reviews",
        "tripadvisor-reviews",
        "trustpilot-reviews",
        "vinted-reviews",
        "walmart-reviews",
        "yelp-reviews-importer",
    }
    tripadvisor = next(app for app in review_apps if app.slug == "tripadvisor-reviews")
    assert tripadvisor.annual_plan_amounts == {Decimal("60.00")}
    assert not any(
        app.annual_plan_amounts
        for app in review_apps
        if app.slug != "tripadvisor-reviews"
    )


def test_catalog_requires_every_referenced_secret(tmp_path):
    path = tmp_path / "apps.yml"
    path.write_text(
        """
organizations:
  - partner_org_id: "1"
    name: Test
    token_env: MISSING_TOKEN
    apps:
      - slug: one
        name: One
        partner_app_id: gid://partners/App/1
"""
    )

    with pytest.raises(CatalogError, match="MISSING_TOKEN"):
        load_catalog(path, {})


def test_catalog_rejects_duplicate_app_identity(tmp_path):
    path = tmp_path / "apps.yml"
    path.write_text(
        """
organizations:
  - partner_org_id: "1"
    name: Test
    token_env: TOKEN
    apps:
      - slug: duplicate
        name: One
        partner_app_id: gid://partners/App/1
      - slug: duplicate
        name: Two
        partner_app_id: gid://partners/App/2
"""
    )

    with pytest.raises(CatalogError, match="Duplicate app slug"):
        load_catalog(path, {"TOKEN": "secret"})


def test_reconciliation_persists_runtime_app_configuration(db):
    path = Path(__file__).parents[1] / "config/apps.yml"
    configured = load_catalog(path, TOKEN_ENV)
    apps = reconcile_catalog(db, configured)

    assert len(apps) == 25
    tax = app_by_slug(db, "eu-tax-exemption-easy", TOKEN_ENV)
    assert tax.id > 0
    assert tax.organization_id > 0
    assert tax.partner_token == "token-3508770"
    assert tax.partner_app_id == "gid://partners/App/287860785153"


def test_reconciliation_refuses_removing_an_active_app(db):
    path = Path(__file__).parents[1] / "config/apps.yml"
    configured = load_catalog(path, TOKEN_ENV)
    reconcile_catalog(db, configured)

    with pytest.raises(CatalogError, match="Active apps cannot be removed"):
        reconcile_catalog(db, configured[:-1])

    inactive = [
        replace(app, active=False) if app == configured[-1] else app
        for app in configured
    ]
    reconcile_catalog(db, inactive)
    remaining = reconcile_catalog(db, configured[:-1])
    assert len(remaining) == 24
