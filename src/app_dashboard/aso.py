"""Reports over Mantle's persisted ASO warehouse."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app_dashboard.catalog import AppConfig
from app_dashboard.display_time import DISPLAY_TIMEZONE


@dataclass(frozen=True)
class KeywordRow:
    keyword: str
    users: int
    install_clicks: int
    average_position: Decimal | None
    latest_position: int | None
    position_change: int | None
    conversion_pct: float
    opportunity_score: int


@dataclass(frozen=True)
class KeywordTotals:
    users: int
    install_clicks: int
    keywords: int
    conversion_pct: float


@dataclass(frozen=True)
class KeywordReport:
    rows: tuple[KeywordRow, ...]
    totals: KeywordTotals


@dataclass(frozen=True)
class PortfolioRow:
    app: AppConfig
    status: str
    users: int
    install_clicks: int
    conversion_pct: float
    top_keyword: str | None
    largest_movement: int | None


@dataclass(frozen=True)
class SourceRow:
    installed_on: object
    shop_domain: str
    source: str
    source_type: str
    source_value: str
    locale: str
    country: str
    device: str


def opportunity_score(clicks: int, latest_position: int | None) -> int:
    if not clicks or latest_position is None or latest_position <= 1:
        return 0
    headroom = min(latest_position - 1, 49) / 49
    intent = min(clicks, 25) / 25
    return round(100 * headroom * intent)


def _bounds(period, previous=False):
    if previous:
        return (
            period.previous_start.astimezone(DISPLAY_TIMEZONE).date(),
            period.previous_end.astimezone(DISPLAY_TIMEZONE).date(),
        )
    return period.display_start, period.display_end


def _facet_sql(facets, alias="k"):
    facets = facets or {}
    clauses = []
    params = []
    for key in ("search_type", "locale", "country", "device"):
        value = facets.get(key)
        if value:
            clauses.append(f"{alias}.{key} = %s")
            params.append(value)
    return (" and " + " and ".join(clauses)) if clauses else "", params


def _keyword_period(conn, app_id, start, end, facets=None):
    extra, params = _facet_sql(facets)
    return conn.execute(
        f"""
        select keyword, sum(users)::int, sum(install_clicks)::int,
               case when sum(position_samples) > 0
                    then sum(average_position * position_samples)
                         / sum(position_samples) end,
               (array_agg(latest_position order by date desc)
                    filter (where latest_position is not null))[1]
        from aso_keyword_daily k
        where k.app_id = %s and k.date >= %s and k.date <= %s {extra}
        group by keyword
        """,
        (app_id, start, end, *params),
    ).fetchall()


def keyword_report(conn, app_id: int, period, facets=None) -> KeywordReport:
    current = _keyword_period(conn, app_id, *_bounds(period), facets)
    previous = {
        row[0]: row for row in _keyword_period(conn, app_id, *_bounds(period, True), facets)
    }
    rows = []
    for keyword, users, clicks, average, latest in current:
        prior_latest = previous.get(keyword, (None, None, None, None, None))[4]
        movement = int(prior_latest - latest) if prior_latest is not None and latest is not None else None
        rows.append(KeywordRow(
            keyword=keyword, users=users, install_clicks=clicks,
            average_position=average, latest_position=latest,
            position_change=movement,
            conversion_pct=round(100 * clicks / users, 1) if users else 0.0,
            opportunity_score=opportunity_score(clicks, latest),
        ))
    rows.sort(key=lambda row: (-row.opportunity_score, -row.install_clicks, row.keyword))
    users = sum(row.users for row in rows)
    clicks = sum(row.install_clicks for row in rows)
    return KeywordReport(
        rows=tuple(rows),
        totals=KeywordTotals(
            users=users, install_clicks=clicks, keywords=len(rows),
            conversion_pct=round(100 * clicks / users, 1) if users else 0.0,
        ),
    )


def portfolio_report(conn, apps: list[AppConfig], period) -> tuple[PortfolioRow, ...]:
    start, end = _bounds(period)
    rows = conn.execute(
        """
        with terms as (
            select app_id, keyword, sum(users)::int users,
                   sum(install_clicks)::int clicks,
                   (array_agg(latest_position order by date desc)
                       filter (where latest_position is not null))[1] latest
            from aso_keyword_daily
            where date >= %s and date <= %s
            group by app_id, keyword
        ), ranked as (
            select *, row_number() over (partition by app_id order by users desc, keyword) rank
            from terms
        )
        select app_id, sum(users)::int, sum(clicks)::int,
               max(keyword) filter (where rank=1)
        from ranked group by app_id
        """,
        (start, end),
    ).fetchall()
    totals = {row[0]: row[1:] for row in rows}
    statuses = dict(conn.execute(
        "select app_id, status from aso_source_capabilities where source='aso_keywords'"
    ).fetchall())
    result = []
    for app in apps:
        users, clicks, top = totals.get(app.id, (0, 0, None))
        status = statuses.get(
            app.id, "not_configured" if not app.ga4_property_id else "pending"
        )
        result.append(PortfolioRow(
            app=app, status=status, users=users, install_clicks=clicks,
            conversion_pct=round(100 * clicks / users, 1) if users else 0.0,
            top_keyword=top, largest_movement=None,
        ))
    return tuple(result)


def install_source_report(conn, app_id: int, period, facets=None) -> tuple[SourceRow, ...]:
    start, end = _bounds(period)
    facets = facets or {}
    clauses = []
    params = []
    for key in ("source", "locale", "country", "device"):
        if facets.get(key):
            clauses.append(f"{key} = %s")
            params.append(facets[key])
    if facets.get("keyword"):
        clauses.append("source_value ilike %s")
        params.append(f"%{facets['keyword']}%")
    extra = " and " + " and ".join(clauses) if clauses else ""
    rows = conn.execute(
        f"""
        select installed_on, shop_domain, source, source_type, source_value,
               locale, country, device
        from aso_install_sources
        where app_id=%s and installed_on >= %s and installed_on <= %s {extra}
        order by installed_on desc, shop_domain
        """,
        (app_id, start, end, *params),
    ).fetchall()
    return tuple(SourceRow(*row) for row in rows)


def position_history(conn, app_id: int, keyword: str, period, facets=None):
    extra, params = _facet_sql(facets)
    start, end = _bounds(period)
    return conn.execute(
        f"""
        select date,
               case when sum(position_samples)>0
                    then sum(average_position * position_samples)
                         / sum(position_samples) end as position
        from aso_keyword_daily k
        where app_id=%s and keyword=%s and date >= %s and date <= %s {extra}
        group by date order by date
        """,
        (app_id, keyword, start, end, *params),
    ).fetchall()
