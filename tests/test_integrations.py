import pytest

from app_dashboard.catalog import list_apps
from app_dashboard.integrations import (
    IntegrationError,
    archive_app,
    integration_rows,
    save_app,
    save_organization,
)


def app_values(organization_id, **overrides):
    values = {
        "organization_id": str(organization_id),
        "partner_app_id": "gid://partners/App/999",
        "slug": "managed-app",
        "name": "Managed App",
        "listing_url": "https://apps.shopify.com/managed-app",
        "listing_locales": "nl, en",
        "annual_plan_amounts": "60.00, 190.00",
        "ga4_property_id": "123456",
        "ga4_credentials_env": "GA4_CREDS",
        "lifecycle_status": "draft",
        "listing_status": "published",
        "listing_status_reason": "",
        "tracking_status": "connected",
    }
    values.update(overrides)
    return values


def test_organization_requires_secret_before_activation(db):
    values = {
        "name": "Reviews", "partner_org_id": "55",
        "token_env": "MISSING_TOKEN", "lifecycle_status": "active",
    }
    with pytest.raises(IntegrationError, match="MISSING_TOKEN"):
        save_organization(db, values, environ={})

    org_id = save_organization(
        db, values, environ={"MISSING_TOKEN": "secret"}
    )
    assert org_id > 0


def test_app_crud_activation_and_archive(db):
    org_id = save_organization(db, {
        "name": "Reviews", "partner_org_id": "55",
        "token_env": "PARTNER_TOKEN", "lifecycle_status": "active",
    }, environ={"PARTNER_TOKEN": "secret"})

    app_id = save_app(db, app_values(org_id))
    draft = integration_rows(db, {"PARTNER_TOKEN": "secret", "GA4_CREDS": "{}"})[-1]
    assert draft["listing_locales"] == ("nl", "en")
    assert draft["partner_token_present"] is True
    assert draft["ga4_credentials_present"] is True

    with pytest.raises(IntegrationError, match="GA4 credentials ENV"):
        save_app(
            db, app_values(org_id, lifecycle_status="active"), app_id,
            environ={"PARTNER_TOKEN": "secret"},
        )

    save_app(
        db, app_values(org_id, lifecycle_status="active"), app_id,
        environ={"PARTNER_TOKEN": "secret", "GA4_CREDS": "{}"},
    )
    runtime = list_apps(db, {"PARTNER_TOKEN": "secret", "GA4_CREDS": "{}"})
    assert [app.slug for app in runtime] == ["managed-app"]
    assert runtime[0].annual_plan_amounts == {60, 190}

    archive_app(db, app_id)
    assert list_apps(db, {"PARTNER_TOKEN": "secret", "GA4_CREDS": "{}"}) == []
    assert integration_rows(db, {})[-1]["archived"] is True


def test_active_app_with_missing_partner_secret_is_not_scheduled(db, test_app):
    db.execute(
        "update apps set lifecycle_status='active', active=true where id=%s",
        (test_app.id,),
    )
    assert list_apps(db, {}) == []
    row = integration_rows(db, {})[0]
    assert row["partner_token_present"] is False


def test_connected_tracking_waits_for_first_ga4_data(db, test_app):
    db.execute(
        "update apps set tracking_status='connected' where id=%s", (test_app.id,)
    )

    assert integration_rows(db, {})[0]["tracking_display_status"] == "awaiting_data"

    db.execute(
        """insert into ga4_daily
               (app_id, date, dimension, value, sessions, users)
           values (%s, '2026-08-12', 'total', 'total', 1, 1)""",
        (test_app.id,),
    )

    assert integration_rows(db, {})[0]["tracking_display_status"] == "connected"


@pytest.mark.parametrize("field,value,message", [
    ("partner_app_id", "987", "Partner app GID"),
    ("ga4_property_id", "G-ABC", "GA4 property ID"),
    ("ga4_credentials_env", "secret-name", "GA4 credentials ENV"),
])
def test_app_rejects_invalid_external_identifiers(db, field, value, message):
    org_id = save_organization(db, {
        "name": "Reviews", "partner_org_id": "55",
        "token_env": "PARTNER_TOKEN", "lifecycle_status": "draft",
    })
    with pytest.raises(IntegrationError, match=message):
        save_app(db, app_values(org_id, **{field: value}))
