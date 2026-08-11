"""Owned GA4 imports for App Store keywords and merchant attribution."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable
from urllib.parse import urlsplit

from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Filter,
    FilterExpression,
    Metric,
    RunReportRequest,
)
from google.api_core.exceptions import (
    GoogleAPICallError,
    ResourceExhausted,
    ServiceUnavailable,
)
from psycopg.types.json import Jsonb

from app_dashboard.catalog import AppConfig
from app_dashboard.config import get_settings

logger = logging.getLogger(__name__)

ASO_LOOKBACK_DAYS = 7
PAGE_SIZE = 100_000
CAPABILITY_SOURCES = ("aso_keywords", "aso_attribution")
FIELD_CANDIDATES = {
    "keyword": ("searchTerm", "customEvent:search_term", "customEvent:keyword"),
    "position": ("customEvent:position", "customEvent:search_position"),
    "shop_domain": ("customEvent:shop_url", "customEvent:shop_domain"),
    "shop_id": ("customEvent:shop_id",),
    "source": ("sessionSource", "firstUserSource", "customEvent:source"),
    "source_type": ("sessionMedium", "firstUserMedium", "customEvent:source_type"),
    "locale": ("language", "customEvent:locale"),
    "country": ("country",),
    "device": ("deviceCategory",),
    "search_type": ("customEvent:search_type", "sessionDefaultChannelGroup"),
}
TRANSIENT_ERRORS = (ResourceExhausted, ServiceUnavailable)


class UnsupportedAsoSource(RuntimeError):
    pass


@dataclass(frozen=True)
class CapabilityReport:
    statuses: dict[str, str]
    fields: dict[str, str]
    missing: dict[str, tuple[str, ...]]


def _status(fields: dict[str, str], required: tuple[str, ...]) -> str:
    present = sum(name in fields for name in required)
    if present == len(required):
        return "ready"
    if present:
        return "partial"
    return "unsupported"


def discover_capabilities(client, property_id: str) -> CapabilityReport:
    metadata = client.get_metadata(name=f"properties/{property_id}/metadata")
    dimensions = {item.api_name for item in metadata.dimensions}
    fields = {
        logical: next(candidate for candidate in candidates if candidate in dimensions)
        for logical, candidates in FIELD_CANDIDATES.items()
        if any(candidate in dimensions for candidate in candidates)
    }
    keyword_required = ("keyword", "position")
    attribution_required = ("shop_domain", "source")
    return CapabilityReport(
        statuses={
            "aso_keywords": _status(fields, keyword_required),
            "aso_attribution": _status(fields, attribution_required),
        },
        fields=fields,
        missing={
            "aso_keywords": tuple(key for key in keyword_required if key not in fields),
            "aso_attribution": tuple(
                key for key in attribution_required if key not in fields
            ),
        },
    )


def _store_capability(conn, app_id: int, source: str, status: str, fields, error=None):
    conn.execute(
        """
        insert into aso_source_capabilities
            (app_id, source, status, fields, checked_at, error_code)
        values (%s, %s, %s, %s, now(), %s)
        on conflict (app_id, source) do update set
            status = excluded.status,
            fields = excluded.fields,
            checked_at = excluded.checked_at,
            error_code = excluded.error_code
        """,
        (app_id, source, status, Jsonb(fields), error),
    )


def sync_capabilities(conn, client, app: AppConfig) -> CapabilityReport:
    try:
        report = discover_capabilities(client, app.ga4_property_id or "")
    except GoogleAPICallError as exc:
        error_code = type(exc).__name__
        for source in CAPABILITY_SOURCES:
            _store_capability(conn, app.id, source, "failed", {}, error_code)
        return CapabilityReport(
            statuses={source: "failed" for source in CAPABILITY_SOURCES},
            fields={},
            missing={source: () for source in CAPABILITY_SOURCES},
        )
    for source in CAPABILITY_SOURCES:
        source_fields = {
            key: value
            for key, value in report.fields.items()
            if key
            in (
                {"keyword", "position", "locale", "country", "device", "search_type"}
                if source == "aso_keywords"
                else {
                    "keyword", "shop_domain", "shop_id", "source", "source_type",
                    "locale", "country", "device",
                }
            )
        }
        _store_capability(conn, app.id, source, report.statuses[source], source_fields)
    return report


def _ga_date(raw: str) -> date:
    return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))


def _run_pages(client, request_factory, sleep: Callable[[float], None] = time.sleep):
    offset = 0
    rows = []
    while True:
        response = None
        for attempt in range(3):
            try:
                response = client.run_report(request_factory(offset))
                break
            except TRANSIENT_ERRORS:
                if attempt == 2:
                    raise
                sleep(2**attempt)
        rows.extend(response.rows)
        row_count = int(getattr(response, "row_count", len(response.rows)))
        offset += len(response.rows)
        if not response.rows or offset >= row_count:
            return rows


def _dimensions(fields: dict[str, str], names: tuple[str, ...]) -> list[tuple[str, str]]:
    return [(name, fields[name]) for name in names if name in fields]


def _event_filter(name: str) -> FilterExpression:
    return FilterExpression(
        filter=Filter(
            field_name="eventName",
            string_filter=Filter.StringFilter(value=name, match_type="EXACT"),
        )
    )


def fetch_keyword_rows(
    client,
    property_id: str,
    fields: dict[str, str],
    start: date,
    end: date,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    if "keyword" not in fields:
        raise UnsupportedAsoSource("keyword dimension is unavailable")
    selected = _dimensions(
        fields, ("keyword", "position", "locale", "country", "device", "search_type")
    )
    ga_dimensions = [Dimension(name="date"), *[Dimension(name=value) for _, value in selected]]
    date_range = DateRange(start_date=start.isoformat(), end_date=end.isoformat())
    prop = f"properties/{property_id}"

    def request(metrics, offset, dimension_filter=None):
        return RunReportRequest(
            property=prop,
            date_ranges=[date_range],
            dimensions=ga_dimensions,
            metrics=[Metric(name=name) for name in metrics],
            dimension_filter=dimension_filter,
            limit=PAGE_SIZE,
            offset=offset,
        )

    traffic = _run_pages(
        client, lambda offset: request(("totalUsers",), offset), sleep
    )
    clicks = _run_pages(
        client,
        lambda offset: request(("eventCount",), offset, _event_filter("Add App button")),
        sleep,
    )
    merged: dict[tuple, dict] = {}

    def read_dimensions(row):
        values = [value.value for value in row.dimension_values]
        raw = {"date": values[0]}
        raw.update({name: values[index + 1] for index, (name, _) in enumerate(selected)})
        return raw

    def key_and_slot(raw):
        keyword = raw.get("keyword", "").strip().casefold()
        key = (
            _ga_date(raw["date"]), keyword, raw.get("locale", ""),
            raw.get("country", ""), raw.get("device", "unknown") or "unknown",
            raw.get("search_type", "search") or "search",
        )
        return key, merged.setdefault(key, {
            "date": key[0], "keyword": key[1], "locale": key[2], "country": key[3],
            "device": key[4], "search_type": key[5], "users": 0,
            "install_clicks": 0, "average_position": None,
            "latest_position": None, "position_samples": 0,
        })

    position_totals: dict[tuple, Decimal] = defaultdict(Decimal)
    for row in traffic:
        raw = read_dimensions(row)
        if not raw.get("keyword", "").strip():
            continue
        key, output = key_and_slot(raw)
        users = int(row.metric_values[0].value or 0)
        output["users"] += users
        if raw.get("position"):
            try:
                position = Decimal(raw["position"])
            except InvalidOperation:
                continue
            samples = max(users, 1)
            position_totals[key] += position * samples
            output["position_samples"] += samples
            output["latest_position"] = round(position)
    for row in clicks:
        raw = read_dimensions(row)
        if not raw.get("keyword", "").strip():
            continue
        _, output = key_and_slot(raw)
        output["install_clicks"] += int(row.metric_values[0].value or 0)
    for key, output in merged.items():
        if output["position_samples"]:
            output["average_position"] = (
                position_totals[key] / output["position_samples"]
            ).quantize(Decimal("0.01"))
    return sorted(merged.values(), key=lambda row: (row["date"], row["keyword"]))


def upsert_keyword_rows(conn, app_id: int, rows: list[dict]) -> int:
    for row in rows:
        conn.execute(
            """
            insert into aso_keyword_daily
                (app_id, date, keyword, locale, country, device, search_type,
                 users, install_clicks, average_position, latest_position,
                 position_samples)
            values (%(app_id)s, %(date)s, %(keyword)s, %(locale)s, %(country)s,
                    %(device)s, %(search_type)s, %(users)s, %(install_clicks)s,
                    %(average_position)s, %(latest_position)s, %(position_samples)s)
            on conflict (app_id, date, keyword, locale, country, device, search_type)
            do update set users=excluded.users,
                          install_clicks=excluded.install_clicks,
                          average_position=excluded.average_position,
                          latest_position=excluded.latest_position,
                          position_samples=excluded.position_samples
            """,
            {**row, "app_id": app_id},
        )
    return len(rows)


def sync_aso_keywords(
    conn, client, app: AppConfig, *, fields=None, today=None, earliest=None,
    force_full=False,
) -> int:
    today = today or date.today()
    earliest = earliest or get_settings().ga4_earliest_data
    existing = conn.execute(
        "select exists(select 1 from aso_keyword_daily where app_id=%s)", (app.id,)
    ).fetchone()[0]
    start = earliest if force_full or not existing else today - timedelta(days=ASO_LOOKBACK_DAYS)
    fields = fields or sync_capabilities(conn, client, app).fields
    rows = fetch_keyword_rows(client, app.ga4_property_id or "", fields, start, today)
    with conn.transaction():
        conn.execute(
            "delete from aso_keyword_daily where app_id=%s and date between %s and %s",
            (app.id, start, today),
        )
        written = upsert_keyword_rows(conn, app.id, rows)
        keyword_fields = {
            key: value
            for key, value in fields.items()
            if key in {"keyword", "position", "locale", "country", "device", "search_type"}
        }
        _store_capability(
            conn,
            app.id,
            "aso_keywords",
            ("ready" if "position" in fields else "partial") if rows else "unsupported",
            keyword_fields,
            None if rows else "NoKeywordValues",
        )
    return written


def normalize_shop_domain(raw: str) -> str:
    value = (raw or "").strip()
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    return (parsed.hostname or "").rstrip(".").casefold()


def normalize_install_source(row: dict) -> dict:
    return {
        "shop_domain": normalize_shop_domain(row.get("shop_domain") or row.get("shop", "")),
        "shop_id": row.get("shop_id") or None,
        "installed_on": date.fromisoformat(str(row["installed_on"])),
        "source": (row.get("source") or "").strip(),
        "source_type": (row.get("source_type") or "").strip(),
        "source_value": (row.get("source_value") or "").strip(),
        "locale": (row.get("locale") or "").strip(),
        "country": (row.get("country") or "").strip(),
        "device": (row.get("device") or "unknown").strip() or "unknown",
    }


def attribution_key(row: dict) -> str:
    normalized = normalize_install_source(row)
    values = [
        normalized[name].isoformat() if isinstance(normalized[name], date) else normalized[name]
        for name in (
            "shop_domain", "installed_on", "source", "source_type", "source_value",
            "locale", "country", "device",
        )
    ]
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


def fetch_install_sources(
    client, property_id: str, fields: dict[str, str], start: date, end: date,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    if "shop_domain" not in fields:
        raise UnsupportedAsoSource("shop_domain dimension is unavailable")
    selected = _dimensions(
        fields,
        ("shop_domain", "shop_id", "source", "source_type", "keyword", "locale", "country", "device"),
    )
    request_dimensions = [Dimension(name="date"), *[Dimension(name=value) for _, value in selected]]
    date_range = DateRange(start_date=start.isoformat(), end_date=end.isoformat())

    def request(offset):
        return RunReportRequest(
            property=f"properties/{property_id}", date_ranges=[date_range],
            dimensions=request_dimensions, metrics=[Metric(name="eventCount")],
            dimension_filter=_event_filter("shopify_app_install"),
            limit=PAGE_SIZE, offset=offset,
        )

    result = []
    for row in _run_pages(client, request, sleep):
        values = [value.value for value in row.dimension_values]
        raw = {name: values[index + 1] for index, (name, _) in enumerate(selected)}
        raw["installed_on"] = _ga_date(values[0])
        raw["source_value"] = raw.pop("keyword", "")
        normalized = normalize_install_source(raw)
        if not normalized["shop_domain"]:
            continue
        normalized["attribution_key"] = attribution_key(normalized)
        result.append(normalized)
    return result


def sync_install_sources(
    conn, client, app: AppConfig, *, fields=None, today=None, earliest=None,
    force_full=False,
) -> int:
    today = today or date.today()
    earliest = earliest or get_settings().ga4_earliest_data
    existing = conn.execute(
        "select exists(select 1 from aso_install_sources where app_id=%s)", (app.id,)
    ).fetchone()[0]
    start = earliest if force_full or not existing else today - timedelta(days=ASO_LOOKBACK_DAYS)
    fields = fields or sync_capabilities(conn, client, app).fields
    rows = fetch_install_sources(client, app.ga4_property_id or "", fields, start, today)
    with conn.transaction():
        conn.execute(
            "delete from aso_install_sources where app_id=%s and installed_on between %s and %s",
            (app.id, start, today),
        )
        for row in rows:
            conn.execute(
                """
                insert into aso_install_sources
                    (app_id, attribution_key, shop_domain, shop_id, installed_on,
                     source, source_type, source_value, locale, country, device)
                values (%(app_id)s, %(attribution_key)s, %(shop_domain)s, %(shop_id)s,
                        %(installed_on)s, %(source)s, %(source_type)s, %(source_value)s,
                        %(locale)s, %(country)s, %(device)s)
                on conflict (app_id, attribution_key) do update set
                    observed_at=now(), shop_id=excluded.shop_id
                """,
                {**row, "app_id": app.id},
            )
    return len(rows)
