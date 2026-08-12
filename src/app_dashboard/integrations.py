"""Database-owned Partner and GA4 integration management."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import psycopg
from psycopg.types.json import Jsonb

from app_dashboard.catalog import LOCALE_RE, SLUG_RE

LIFECYCLE_STATUSES = ("draft", "ready", "active", "blocked")
LISTING_STATUSES = ("unknown", "draft", "submitted", "in_review", "published", "blocked")
TRACKING_STATUSES = ("unknown", "pending", "connected", "blocked")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PARTNER_APP_RE = re.compile(r"^gid://partners/App/[0-9]+$")


class IntegrationError(ValueError):
    """Submitted integration metadata cannot be stored or activated."""


@dataclass(frozen=True)
class OrganizationRecord:
    id: int
    partner_org_id: str
    name: str
    token_env: str
    lifecycle_status: str
    archived: bool
    token_present: bool


def secret_present(name: str | None, environ: Mapping[str, str] = os.environ) -> bool:
    return bool(name and environ.get(name, "").strip())


def _text(value: str | None, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise IntegrationError(f"{label} is required")
    return cleaned


def _choice(value: str | None, choices: tuple[str, ...], label: str) -> str:
    cleaned = _text(value, label)
    if cleaned not in choices:
        raise IntegrationError(f"Unknown {label.lower()}")
    return cleaned


def _prices(raw: str | None) -> list[str]:
    values: set[Decimal] = set()
    for item in re.split(r"[,\n]", raw or ""):
        item = item.strip()
        if not item:
            continue
        try:
            value = Decimal(item)
        except InvalidOperation:
            raise IntegrationError(f"Invalid annual plan amount: {item}") from None
        if not value.is_finite() or value <= 0:
            raise IntegrationError("Annual plan amounts must be positive")
        values.add(value)
    return [str(value) for value in sorted(values)]


def _locales(raw: str | None) -> list[str]:
    values = [item.strip() for item in re.split(r"[,\n]", raw or "") if item.strip()]
    if not values:
        raise IntegrationError("At least one listing locale is required")
    if len(values) != len(set(values)) or any(not LOCALE_RE.fullmatch(v) for v in values):
        raise IntegrationError("Listing locales must be unique codes such as en or nl-NL")
    return values


def list_organizations(
    conn: psycopg.Connection, environ: Mapping[str, str] = os.environ
) -> list[OrganizationRecord]:
    rows = conn.execute(
        """
        select id, partner_org_id, name, token_env, lifecycle_status,
               archived_at is not null
        from organizations
        order by archived_at nulls first, name
        """
    ).fetchall()
    return [
        OrganizationRecord(*row, token_present=secret_present(row[3], environ))
        for row in rows
    ]


def integration_rows(
    conn: psycopg.Connection, environ: Mapping[str, str] = os.environ
) -> list[dict]:
    rows = conn.execute(
        """
        select a.id, a.slug, a.name, a.partner_app_id, a.lifecycle_status,
               a.listing_url, a.listing_locales, a.listing_status,
               a.listing_status_reason, a.ga4_property_id,
               a.ga4_credentials_env, a.tracking_status, a.active,
               a.archived_at, a.updated_at, o.id, o.name, o.partner_org_id,
               o.token_env, o.archived_at,
               exists(select 1 from ga4_daily g where g.app_id=a.id) as has_ga4_data,
               a.review_prompt_enabled
        from apps a
        join organizations o on o.id = a.organization_id
        order by a.archived_at nulls first, o.name, a.name
        """
    ).fetchall()
    result = []
    for row in rows:
        tracking_display_status = (
            "awaiting_data"
            if row[11] == "connected" and not row[20]
            else row[11]
        )
        result.append({
            "id": row[0], "slug": row[1], "name": row[2],
            "partner_app_id": row[3], "lifecycle_status": row[4],
            "listing_url": row[5], "listing_locales": tuple(row[6]),
            "listing_status": row[7], "listing_status_reason": row[8],
            "ga4_property_id": row[9], "ga4_credentials_env": row[10],
            "tracking_status": row[11],
            "tracking_display_status": tracking_display_status,
            "active": row[12],
            "archived": row[13] is not None, "updated_at": row[14],
            "organization_id": row[15], "organization_name": row[16],
            "partner_org_id": row[17], "partner_token_env": row[18],
            "organization_archived": row[19] is not None,
            "partner_token_present": secret_present(row[18], environ),
            "ga4_credentials_present": secret_present(row[10], environ),
            "review_prompt_enabled": row[21],
        })
    return result


def get_app(conn: psycopg.Connection, app_id: int) -> dict:
    row = conn.execute(
        """
        select id, organization_id, partner_app_id, slug, name, listing_url,
               listing_locales, annual_plan_amounts, usage_token_env,
               usage_event_types, usage_activation_event, usage_live_event,
               ga4_property_id, ga4_credentials_env, lifecycle_status,
               listing_status, listing_status_reason, tracking_status,
               active, archived_at, review_prompt_enabled, review_trigger_event,
               review_min_success_count, review_min_install_hours,
               review_retry_days, review_annual_cap
        from apps where id=%s
        """,
        (app_id,),
    ).fetchone()
    if row is None:
        raise KeyError(app_id)
    keys = (
        "id", "organization_id", "partner_app_id", "slug", "name", "listing_url",
        "listing_locales", "annual_plan_amounts", "usage_token_env",
        "usage_event_types", "usage_activation_event", "usage_live_event",
        "ga4_property_id", "ga4_credentials_env", "lifecycle_status",
        "listing_status", "listing_status_reason", "tracking_status", "active",
        "archived_at",
        "review_prompt_enabled", "review_trigger_event",
        "review_min_success_count", "review_min_install_hours",
        "review_retry_days", "review_annual_cap",
    )
    return dict(zip(keys, row, strict=True))


def save_organization(
    conn: psycopg.Connection,
    data: Mapping[str, str],
    org_id: int | None = None,
    environ: Mapping[str, str] = os.environ,
) -> int:
    name = _text(data.get("name"), "Organization name")
    partner_org_id = _text(data.get("partner_org_id"), "Partner organization ID")
    if not partner_org_id.isdigit():
        raise IntegrationError("Partner organization ID must be numeric")
    token_env = _text(data.get("token_env"), "Partner token ENV name")
    if not ENV_RE.fullmatch(token_env):
        raise IntegrationError("Partner token ENV name must use uppercase letters, numbers, and underscores")
    status = _choice(data.get("lifecycle_status", "draft"), LIFECYCLE_STATUSES, "Status")
    if status in {"ready", "active"} and not secret_present(token_env, environ):
        raise IntegrationError(f"Partner token ENV {token_env} is missing")
    try:
        if org_id is None:
            return conn.execute(
                """insert into organizations
                   (partner_org_id, name, token_env, lifecycle_status, active)
                   values (%s,%s,%s,%s,%s) returning id""",
                (partner_org_id, name, token_env, status, status == "active"),
            ).fetchone()[0]
        changed = conn.execute(
            """update organizations set partner_org_id=%s, name=%s, token_env=%s,
               lifecycle_status=%s, active=%s, updated_at=now()
               where id=%s and archived_at is null returning id""",
            (partner_org_id, name, token_env, status, status == "active", org_id),
        ).fetchone()
        if changed is None:
            raise KeyError(org_id)
        return changed[0]
    except psycopg.errors.UniqueViolation:
        raise IntegrationError("Partner organization ID already exists") from None


def save_app(
    conn: psycopg.Connection,
    data: Mapping[str, str],
    app_id: int | None = None,
    environ: Mapping[str, str] = os.environ,
) -> int:
    name = _text(data.get("name"), "App name")
    slug = _text(data.get("slug"), "Slug")
    if not SLUG_RE.fullmatch(slug):
        raise IntegrationError("Slug must contain lowercase letters, numbers, and single hyphens")
    partner_app_id = _text(data.get("partner_app_id"), "Partner app GID")
    if not PARTNER_APP_RE.fullmatch(partner_app_id):
        raise IntegrationError("Partner app GID must look like gid://partners/App/123")
    try:
        organization_id = int(_text(data.get("organization_id"), "Organization"))
    except ValueError:
        raise IntegrationError("Organization is invalid") from None
    if conn.execute(
        "select 1 from organizations where id=%s and archived_at is null", (organization_id,)
    ).fetchone() is None:
        raise IntegrationError("Organization is unavailable")

    status = _choice(data.get("lifecycle_status", "draft"), LIFECYCLE_STATUSES, "Status")
    listing_status = _choice(data.get("listing_status", "unknown"), LISTING_STATUSES, "Listing status")
    tracking_status = _choice(data.get("tracking_status", "unknown"), TRACKING_STATUSES, "Tracking status")
    locales = _locales(data.get("listing_locales", "en"))
    annual = _prices(data.get("annual_plan_amounts"))
    listing_url = (data.get("listing_url") or "").strip() or None
    ga4_property_id = (data.get("ga4_property_id") or "").strip() or None
    ga4_credentials_env = (data.get("ga4_credentials_env") or "").strip() or None
    if ga4_property_id and not ga4_property_id.isdigit():
        raise IntegrationError("GA4 property ID must be numeric")
    if ga4_credentials_env and not ENV_RE.fullmatch(ga4_credentials_env):
        raise IntegrationError("GA4 credentials ENV must use uppercase letters, numbers, and underscores")
    listing_reason = (data.get("listing_status_reason") or "").strip() or None
    review_enabled = data.get("review_prompt_enabled") in {"1", "true", "on", "yes"}
    review_trigger = (data.get("review_trigger_event") or "").strip() or None
    usage_token_env = (data.get("usage_token_env") or "").strip() or None
    usage_events = [item.strip() for item in (data.get("usage_event_types") or "").split(",") if item.strip()]
    def positive_int(field: str, default: int, minimum: int, maximum: int | None = None) -> int:
        try:
            value = int(data.get(field) or default)
        except ValueError:
            raise IntegrationError(f"{field.replace('_', ' ').title()} must be a number") from None
        if value < minimum or (maximum is not None and value > maximum):
            raise IntegrationError(f"{field.replace('_', ' ').title()} is outside its allowed range")
        return value
    review_min_success = positive_int("review_min_success_count", 1, 1)
    review_install_hours = positive_int("review_min_install_hours", 24, 24)
    review_retry_days = positive_int("review_retry_days", 90, 1)
    review_annual_cap = positive_int("review_annual_cap", 3, 1, 3)
    if review_trigger and review_trigger not in usage_events:
        raise IntegrationError("Review trigger event must be one of the usage event types")
    if review_enabled and (not usage_token_env or not review_trigger):
        raise IntegrationError("Review collection requires a usage token ENV and trigger event")

    if status in {"ready", "active"}:
        org = conn.execute(
            "select token_env from organizations where id=%s", (organization_id,)
        ).fetchone()
        errors = []
        if not secret_present(org[0], environ):
            errors.append(f"Partner token ENV {org[0]} is missing")
        if not ga4_property_id:
            errors.append("GA4 property ID is required")
        if not ga4_credentials_env or not secret_present(ga4_credentials_env, environ):
            errors.append("GA4 credentials ENV is missing")
        if listing_status == "unknown":
            errors.append("Choose the Shopify listing status")
        if tracking_status == "unknown":
            errors.append("Choose the Measurement Protocol status")
        if errors:
            raise IntegrationError(". ".join(errors))

    values = (
        organization_id, partner_app_id, slug, name, listing_url, Jsonb(locales),
        Jsonb(annual), ga4_property_id, ga4_credentials_env, status, listing_status,
        listing_reason, tracking_status, status == "active", usage_token_env,
        Jsonb(usage_events), review_enabled, review_trigger, review_min_success,
        review_install_hours, review_retry_days, review_annual_cap,
    )
    try:
        if app_id is None:
            return conn.execute(
                """insert into apps (
                    organization_id, partner_app_id, slug, name, listing_url,
                    listing_locales, annual_plan_amounts, ga4_property_id,
                    ga4_credentials_env, lifecycle_status, listing_status,
                    listing_status_reason, tracking_status, active,
                    usage_token_env, usage_event_types, review_prompt_enabled,
                    review_trigger_event, review_min_success_count,
                    review_min_install_hours, review_retry_days, review_annual_cap)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   returning id""", values,
            ).fetchone()[0]
        changed = conn.execute(
            """update apps set organization_id=%s, partner_app_id=%s, slug=%s,
               name=%s, listing_url=%s, listing_locales=%s,
               annual_plan_amounts=%s, ga4_property_id=%s, ga4_credentials_env=%s,
               lifecycle_status=%s, listing_status=%s, listing_status_reason=%s,
               tracking_status=%s, active=%s, updated_at=now(),
               usage_token_env=%s, usage_event_types=%s,
               review_prompt_enabled=%s, review_trigger_event=%s,
               review_min_success_count=%s, review_min_install_hours=%s,
               review_retry_days=%s, review_annual_cap=%s
               where id=%s and archived_at is null returning id""",
            values + (app_id,),
        ).fetchone()
        if changed is None:
            raise KeyError(app_id)
        return changed[0]
    except psycopg.errors.UniqueViolation:
        raise IntegrationError("Slug or Partner app GID already exists") from None


def archive_app(conn: psycopg.Connection, app_id: int) -> None:
    changed = conn.execute(
        """update apps set active=false, lifecycle_status='blocked',
           archived_at=coalesce(archived_at, now()), updated_at=now()
           where id=%s returning id""",
        (app_id,),
    ).fetchone()
    if changed is None:
        raise KeyError(app_id)


def archive_organization(conn: psycopg.Connection, org_id: int) -> None:
    changed = conn.execute(
        """update organizations set active=false, lifecycle_status='blocked',
           archived_at=coalesce(archived_at, now()), updated_at=now()
           where id=%s returning id""",
        (org_id,),
    ).fetchone()
    if changed is None:
        raise KeyError(org_id)
    conn.execute(
        """update apps set active=false, lifecycle_status='blocked',
           archived_at=coalesce(archived_at, now()), updated_at=now()
           where organization_id=%s""",
        (org_id,),
    )
