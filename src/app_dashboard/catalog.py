"""Versioned multi-app catalog and its database reconciliation."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg
import yaml
from psycopg.types.json import Jsonb

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")


class CatalogError(ValueError):
    """The app catalog is incomplete, invalid, or conflicts with stored state."""


@dataclass(frozen=True, kw_only=True)
class AppSpec:
    slug: str
    name: str
    partner_app_id: str
    partner_org_id: str
    organization_name: str
    partner_token_env: str
    partner_token: str
    annual_plan_amounts: frozenset[Decimal]
    listing_url: str | None
    listing_locales: tuple[str, ...]
    usage_token_env: str | None
    usage_token: str | None
    usage_event_types: frozenset[str]
    usage_activation_event: str | None
    usage_live_event: str | None
    ga4_property_id: str | None
    ga4_credentials_env: str | None
    ga4_credentials_json: str | None
    review_prompt_enabled: bool = False
    review_trigger_event: str | None = None
    review_min_success_count: int = 1
    review_min_install_hours: int = 24
    review_retry_days: int = 90
    review_annual_cap: int = 3
    active: bool = True


@dataclass(frozen=True, kw_only=True)
class AppConfig(AppSpec):
    id: int
    organization_id: int


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, label: str) -> str | None:
    if value is None or value == "":
        return None
    return _required_text(value, label)


def _resolve_env(name: str | None, environ: Mapping[str, str], label: str) -> str | None:
    if name is None:
        return None
    value = environ.get(name, "").strip()
    if not value:
        raise CatalogError(f"{label} references unset or empty environment variable {name}")
    return value


def _prices(raw: Any, label: str) -> frozenset[Decimal]:
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise CatalogError(f"{label} must be a list of positive decimal prices")

    result: set[Decimal] = set()
    for item in raw:
        try:
            value = Decimal(str(item))
        except (InvalidOperation, ValueError):
            raise CatalogError(f"{label} contains invalid price {item!r}") from None
        if not value.is_finite() or value <= 0:
            raise CatalogError(f"{label} contains non-positive or non-finite price {item!r}")
        result.add(value)
    return frozenset(result)


def _string_set(raw: Any, label: str) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise CatalogError(f"{label} must be a list of strings")
    values = frozenset(_required_text(item, label) for item in raw)
    if len(values) != len(raw):
        raise CatalogError(f"{label} contains duplicate values")
    return values


def _listing_locales(raw: Any, label: str) -> tuple[str, ...]:
    if raw is None:
        return ("en",)
    if not isinstance(raw, list) or not raw:
        raise CatalogError(f"{label} must be a non-empty list of locale codes")
    locales = tuple(_required_text(item, label) for item in raw)
    if len(set(locales)) != len(locales):
        raise CatalogError(f"{label} contains duplicate values")
    if any(not LOCALE_RE.fullmatch(locale) for locale in locales):
        raise CatalogError(f"{label} contains an invalid locale code")
    return locales


def load_catalog(
    path: str | Path, environ: Mapping[str, str] = os.environ
) -> list[AppSpec]:
    """Load and fully validate a catalog, resolving secrets from the environment."""
    catalog_path = Path(path)
    try:
        document = yaml.safe_load(catalog_path.read_text())
    except OSError as exc:
        raise CatalogError(f"Cannot read app catalog {catalog_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CatalogError(f"Invalid YAML in app catalog {catalog_path}: {exc}") from exc

    if not isinstance(document, dict) or not isinstance(document.get("organizations"), list):
        raise CatalogError("App catalog must contain an organizations list")

    specs: list[AppSpec] = []
    seen_orgs: set[str] = set()
    seen_slugs: set[str] = set()
    seen_app_ids: set[str] = set()

    for org_index, raw_org in enumerate(document["organizations"]):
        prefix = f"organizations[{org_index}]"
        if not isinstance(raw_org, dict):
            raise CatalogError(f"{prefix} must be a mapping")
        partner_org_id = _required_text(raw_org.get("partner_org_id"), f"{prefix}.partner_org_id")
        organization_name = _required_text(raw_org.get("name"), f"{prefix}.name")
        token_env = _required_text(raw_org.get("token_env"), f"{prefix}.token_env")
        token = _resolve_env(token_env, environ, f"{prefix}.token_env")
        if partner_org_id in seen_orgs:
            raise CatalogError(f"Duplicate partner organization id {partner_org_id}")
        seen_orgs.add(partner_org_id)

        raw_apps = raw_org.get("apps")
        if not isinstance(raw_apps, list) or not raw_apps:
            raise CatalogError(f"{prefix}.apps must be a non-empty list")
        org_active = raw_org.get("active", True)
        if not isinstance(org_active, bool):
            raise CatalogError(f"{prefix}.active must be true or false")

        for app_index, raw_app in enumerate(raw_apps):
            app_prefix = f"{prefix}.apps[{app_index}]"
            if not isinstance(raw_app, dict):
                raise CatalogError(f"{app_prefix} must be a mapping")
            slug = _required_text(raw_app.get("slug"), f"{app_prefix}.slug")
            if not SLUG_RE.fullmatch(slug):
                raise CatalogError(
                    f"{app_prefix}.slug must contain lowercase letters, numbers, and single hyphens"
                )
            partner_app_id = _required_text(
                raw_app.get("partner_app_id"), f"{app_prefix}.partner_app_id"
            )
            if slug in seen_slugs:
                raise CatalogError(f"Duplicate app slug {slug}")
            if partner_app_id in seen_app_ids:
                raise CatalogError(f"Duplicate Partner app id {partner_app_id}")
            seen_slugs.add(slug)
            seen_app_ids.add(partner_app_id)

            active = raw_app.get("active", org_active)
            if not isinstance(active, bool):
                raise CatalogError(f"{app_prefix}.active must be true or false")

            usage = raw_app.get("usage") or {}
            if not isinstance(usage, dict):
                raise CatalogError(f"{app_prefix}.usage must be a mapping")
            usage_token_env = _optional_text(
                usage.get("token_env"), f"{app_prefix}.usage.token_env"
            )
            event_types = _string_set(
                usage.get("event_types"), f"{app_prefix}.usage.event_types"
            )
            activation_event = _optional_text(
                usage.get("activation_event"), f"{app_prefix}.usage.activation_event"
            )
            live_event = _optional_text(
                usage.get("live_event"), f"{app_prefix}.usage.live_event"
            )
            for label, value in (("activation_event", activation_event), ("live_event", live_event)):
                if value is not None and value not in event_types:
                    raise CatalogError(
                        f"{app_prefix}.usage.{label} is not present in usage.event_types"
                    )

            ga4 = raw_app.get("ga4") or {}
            if not isinstance(ga4, dict):
                raise CatalogError(f"{app_prefix}.ga4 must be a mapping")
            ga4_property_id = _optional_text(
                ga4.get("property_id"), f"{app_prefix}.ga4.property_id"
            )
            ga4_credentials_env = _optional_text(
                ga4.get("credentials_env"), f"{app_prefix}.ga4.credentials_env"
            )
            if (ga4_property_id is None) != (ga4_credentials_env is None):
                raise CatalogError(
                    f"{app_prefix}.ga4 requires both property_id and credentials_env"
                )

            review = raw_app.get("review_prompt") or {}
            if not isinstance(review, dict):
                raise CatalogError(f"{app_prefix}.review_prompt must be a mapping")
            review_enabled = review.get("enabled", False)
            if not isinstance(review_enabled, bool):
                raise CatalogError(f"{app_prefix}.review_prompt.enabled must be true or false")
            review_trigger = _optional_text(
                review.get("trigger_event"), f"{app_prefix}.review_prompt.trigger_event"
            )
            if review_trigger is not None and review_trigger not in event_types:
                raise CatalogError(
                    f"{app_prefix}.review_prompt.trigger_event is not present in usage.event_types"
                )
            def review_int(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
                value = review.get(name, default)
                if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                    raise CatalogError(f"{app_prefix}.review_prompt.{name} is invalid")
                if maximum is not None and value > maximum:
                    raise CatalogError(f"{app_prefix}.review_prompt.{name} is invalid")
                return value
            review_min_success = review_int("min_success_count", 1, 1)
            review_install_hours = review_int("min_install_hours", 24, 24)
            review_retry_days = review_int("retry_days", 90, 1)
            review_annual_cap = review_int("annual_cap", 3, 1, 3)
            if review_enabled and (not usage_token_env or not review_trigger):
                raise CatalogError(
                    f"{app_prefix}.review_prompt requires usage.token_env and trigger_event"
                )

            specs.append(
                AppSpec(
                    slug=slug,
                    name=_required_text(raw_app.get("name"), f"{app_prefix}.name"),
                    partner_app_id=partner_app_id,
                    partner_org_id=partner_org_id,
                    organization_name=organization_name,
                    partner_token_env=token_env,
                    partner_token=token or "",
                    annual_plan_amounts=_prices(
                        raw_app.get("annual_plan_amounts"),
                        f"{app_prefix}.annual_plan_amounts",
                    ),
                    listing_url=_optional_text(
                        raw_app.get("listing_url"), f"{app_prefix}.listing_url"
                    ),
                    listing_locales=_listing_locales(
                        raw_app.get("listing_locales"), f"{app_prefix}.listing_locales"
                    ),
                    usage_token_env=usage_token_env,
                    usage_token=_resolve_env(
                        usage_token_env, environ, f"{app_prefix}.usage.token_env"
                    ),
                    usage_event_types=event_types,
                    usage_activation_event=activation_event,
                    usage_live_event=live_event,
                    ga4_property_id=ga4_property_id,
                    ga4_credentials_env=ga4_credentials_env,
                    ga4_credentials_json=_resolve_env(
                        ga4_credentials_env,
                        environ,
                        f"{app_prefix}.ga4.credentials_env",
                    ),
                    review_prompt_enabled=review_enabled,
                    review_trigger_event=review_trigger,
                    review_min_success_count=review_min_success,
                    review_min_install_hours=review_install_hours,
                    review_retry_days=review_retry_days,
                    review_annual_cap=review_annual_cap,
                    active=active,
                )
            )

    if not specs:
        raise CatalogError("App catalog contains no apps")
    return specs


def reconcile_catalog(
    conn: psycopg.Connection, configured: list[AppSpec]
) -> list[AppConfig]:
    """Upsert catalog metadata while refusing accidental removal of an active app."""
    if not configured:
        raise CatalogError("Cannot reconcile an empty app catalog")

    configured_ids = [app.partner_app_id for app in configured]
    missing = conn.execute(
        """
        select name, partner_app_id
        from apps
        where active and not (partner_app_id = any(%s))
        order by name
        """,
        (configured_ids,),
    ).fetchall()
    if missing:
        labels = ", ".join(f"{name} ({app_id})" for name, app_id in missing)
        raise CatalogError(
            "Active apps cannot be removed from config. Mark them active: false first: "
            + labels
        )

    with conn.transaction():
        organization_ids: dict[str, int] = {}
        for app in configured:
            if app.partner_org_id in organization_ids:
                continue
            row = conn.execute(
                """
                insert into organizations (partner_org_id, name, token_env, active)
                values (%s, %s, %s, true)
                on conflict (partner_org_id) do update set
                    name = excluded.name,
                    token_env = excluded.token_env,
                    active = true,
                    updated_at = now()
                returning id
                """,
                (app.partner_org_id, app.organization_name, app.partner_token_env),
            ).fetchone()
            organization_ids[app.partner_org_id] = row[0]

        for app in configured:
            conn.execute(
                """
                insert into apps (
                    organization_id, partner_app_id, slug, name, listing_url,
                    listing_locales, annual_plan_amounts, usage_token_env, usage_event_types,
                    usage_activation_event, usage_live_event, ga4_property_id,
                    ga4_credentials_env, review_prompt_enabled,
                    review_trigger_event, review_min_success_count,
                    review_min_install_hours, review_retry_days, review_annual_cap, active
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s)
                on conflict (partner_app_id) do update set
                    organization_id = excluded.organization_id,
                    slug = excluded.slug,
                    name = excluded.name,
                    listing_url = excluded.listing_url,
                    listing_locales = excluded.listing_locales,
                    annual_plan_amounts = excluded.annual_plan_amounts,
                    usage_token_env = excluded.usage_token_env,
                    usage_event_types = excluded.usage_event_types,
                    usage_activation_event = excluded.usage_activation_event,
                    usage_live_event = excluded.usage_live_event,
                    ga4_property_id = excluded.ga4_property_id,
                    ga4_credentials_env = excluded.ga4_credentials_env,
                    review_prompt_enabled = excluded.review_prompt_enabled,
                    review_trigger_event = excluded.review_trigger_event,
                    review_min_success_count = excluded.review_min_success_count,
                    review_min_install_hours = excluded.review_min_install_hours,
                    review_retry_days = excluded.review_retry_days,
                    review_annual_cap = excluded.review_annual_cap,
                    active = excluded.active,
                    updated_at = now()
                """,
                (
                    organization_ids[app.partner_org_id],
                    app.partner_app_id,
                    app.slug,
                    app.name,
                    app.listing_url,
                    Jsonb(list(app.listing_locales)),
                    Jsonb([str(value) for value in sorted(app.annual_plan_amounts)]),
                    app.usage_token_env,
                    Jsonb(sorted(app.usage_event_types)),
                    app.usage_activation_event,
                    app.usage_live_event,
                    app.ga4_property_id,
                    app.ga4_credentials_env,
                    app.review_prompt_enabled,
                    app.review_trigger_event,
                    app.review_min_success_count,
                    app.review_min_install_hours,
                    app.review_retry_days,
                    app.review_annual_cap,
                    app.active,
                ),
            )

    ids = {
        partner_app_id: (app_id, organization_id)
        for partner_app_id, app_id, organization_id in conn.execute(
            """
            select partner_app_id, id, organization_id
            from apps
            where partner_app_id = any(%s)
            """,
            (configured_ids,),
        ).fetchall()
    }
    return [
        AppConfig(
            id=ids[app.partner_app_id][0],
            organization_id=ids[app.partner_app_id][1],
            **{name: getattr(app, name) for name in AppSpec.__dataclass_fields__},
        )
        for app in configured
        if app.active
    ]


def list_apps(
    conn: psycopg.Connection,
    environ: Mapping[str, str] = os.environ,
    *,
    active_only: bool = True,
) -> list[AppConfig]:
    where = (
        "where a.active and o.active and a.lifecycle_status = 'active' "
        "and a.archived_at is null and o.archived_at is null"
        if active_only else ""
    )
    rows = conn.execute(
        f"""
        select
            a.id, a.organization_id, a.slug, a.name, a.partner_app_id,
            o.partner_org_id, o.name, o.token_env, a.annual_plan_amounts,
            a.listing_url, a.listing_locales, a.usage_token_env, a.usage_event_types,
            a.usage_activation_event, a.usage_live_event, a.ga4_property_id,
            a.ga4_credentials_env, a.review_prompt_enabled,
            a.review_trigger_event, a.review_min_success_count,
            a.review_min_install_hours, a.review_retry_days, a.review_annual_cap,
            a.active
        from apps a
        join organizations o on o.id = a.organization_id
        {where}
        order by a.name, a.id
        """
    ).fetchall()

    result: list[AppConfig] = []
    for row in rows:
        token_env = row[7]
        usage_token_env = row[11]
        ga4_credentials_env = row[16]
        # An active row whose Partner secret disappeared is operationally
        # blocked. Keep it visible in management, but never hand it to a sync.
        if active_only and not bool(environ.get(token_env, "").strip()):
            continue
        result.append(
            AppConfig(
                id=row[0],
                organization_id=row[1],
                slug=row[2],
                name=row[3],
                partner_app_id=row[4],
                partner_org_id=row[5],
                organization_name=row[6],
                partner_token_env=token_env,
                partner_token=(environ.get(token_env, "").strip() if token_env else ""),
                annual_plan_amounts=frozenset(Decimal(value) for value in row[8]),
                listing_url=row[9],
                listing_locales=tuple(row[10]),
                usage_token_env=usage_token_env,
                usage_token=(
                    environ.get(usage_token_env, "").strip()
                    if usage_token_env and environ.get(usage_token_env, "").strip()
                    else None
                ),
                usage_event_types=frozenset(row[12]),
                usage_activation_event=row[13],
                usage_live_event=row[14],
                ga4_property_id=row[15],
                ga4_credentials_env=ga4_credentials_env,
                ga4_credentials_json=(
                    environ.get(ga4_credentials_env, "").strip()
                    if ga4_credentials_env and environ.get(ga4_credentials_env, "").strip()
                    else None
                ),
                review_prompt_enabled=row[17],
                review_trigger_event=row[18],
                review_min_success_count=row[19],
                review_min_install_hours=row[20],
                review_retry_days=row[21],
                review_annual_cap=row[22],
                active=row[23],
            )
        )
    return result


def app_by_slug(
    conn: psycopg.Connection,
    slug: str,
    environ: Mapping[str, str] = os.environ,
) -> AppConfig:
    for app in list_apps(conn, environ):
        if app.slug == slug:
            return app
    raise CatalogError(f"Unknown or inactive app slug {slug!r}")
