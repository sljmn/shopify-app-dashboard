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


@dataclass(frozen=True)
class ListingChangeRow:
    changed_at: object
    locale: str
    field: str
    before_value: object
    after_value: object
    improved: int
    declined: int
    unchanged: int


@dataclass(frozen=True)
class ResearchRow:
    keyword: str
    source: str
    first_seen_at: object
    last_seen_at: object
    in_listing: bool
    in_traffic: bool


ASO_SORTS = {
    "portfolio": {
        "app": (lambda row: row.app.name.casefold(), "asc"),
        "status": (lambda row: row.status.casefold(), "asc"),
        "users": (lambda row: row.users, "desc"),
        "clicks": (lambda row: row.install_clicks, "desc"),
        "conversion": (lambda row: row.conversion_pct, "desc"),
        "keyword": (
            lambda row: row.top_keyword.casefold() if row.top_keyword else None,
            "asc",
        ),
    },
    "keywords": {
        "keyword": (lambda row: row.keyword.casefold(), "asc"),
        "users": (lambda row: row.users, "desc"),
        "clicks": (lambda row: row.install_clicks, "desc"),
        "conversion": (lambda row: row.conversion_pct, "desc"),
        "latest": (lambda row: row.latest_position, "asc"),
        "movement": (lambda row: row.position_change, "desc"),
        "opportunity": (lambda row: row.opportunity_score, "desc"),
    },
    "history": {
        "date": (lambda row: row[0], "asc"),
        "position": (lambda row: row[1], "asc"),
    },
    "sources": {
        "date": (lambda row: row.installed_on, "desc"),
        "shop": (lambda row: row.shop_domain.casefold(), "asc"),
        "source": (lambda row: row.source.casefold(), "asc"),
        "type": (lambda row: row.source_type.casefold(), "asc"),
        "value": (lambda row: row.source_value.casefold() if row.source_value else None, "asc"),
        "locale": (lambda row: row.locale.casefold() if row.locale else None, "asc"),
        "country": (lambda row: row.country.casefold() if row.country else None, "asc"),
        "device": (lambda row: row.device.casefold(), "asc"),
    },
    "listing": {
        "when": (lambda row: row.changed_at, "desc"),
        "locale": (lambda row: row.locale.casefold(), "asc"),
        "field": (lambda row: row.field.casefold(), "asc"),
        "before": (lambda row: str(row.before_value).casefold(), "asc"),
        "after": (lambda row: str(row.after_value).casefold(), "asc"),
        "movement": (
            lambda row: (row.improved - row.declined, row.unchanged), "desc",
        ),
    },
    "research": {
        "keyword": (lambda row: row.keyword.casefold(), "asc"),
        "source": (lambda row: row.source.casefold(), "asc"),
        "first_seen": (lambda row: row.first_seen_at, "desc"),
        "last_seen": (lambda row: row.last_seen_at, "desc"),
        "listing": (lambda row: row.in_listing, "desc"),
        "traffic": (lambda row: row.in_traffic, "desc"),
    },
}


def sort_aso_rows(
    rows, table: str, sort: str | None, direction: str | None, *,
    return_state: bool = False,
):
    definitions = ASO_SORTS[table]
    default_key = next(iter(definitions))
    key = sort if sort in definitions else default_key
    getter, default_direction = definitions[key]
    selected_direction = direction if direction in {"asc", "desc"} else default_direction
    present = [row for row in rows if getter(row) is not None]
    missing = [row for row in rows if getter(row) is None]
    ordered = tuple(sorted(
        present, key=getter, reverse=selected_direction == "desc",
    ) + missing)
    if return_state:
        return ordered, key, selected_direction
    return ordered


def aso_sort_direction(table: str, key: str) -> str:
    return ASO_SORTS[table][key][1]


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
          and btrim(k.keyword) <> ''
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
              and btrim(keyword) <> ''
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
    previous_start, previous_end = _bounds(period, True)
    movements = dict(conn.execute(
        """
        with current_positions as (
            select distinct on (app_id,keyword) app_id,keyword,latest_position
            from aso_keyword_daily
            where date >= %s and date <= %s and latest_position is not null
            order by app_id,keyword,date desc
        ), previous_positions as (
            select distinct on (app_id,keyword) app_id,keyword,latest_position
            from aso_keyword_daily
            where date >= %s and date <= %s and latest_position is not null
            order by app_id,keyword,date desc
        ), changes as (
            select c.app_id, p.latest_position-c.latest_position movement
            from current_positions c join previous_positions p using (app_id,keyword)
        )
        select app_id,(array_agg(movement order by abs(movement) desc))[1]
        from changes group by app_id
        """,
        (start, end, previous_start, previous_end),
    ).fetchall())
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
            top_keyword=top, largest_movement=movements.get(app.id),
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


def listing_history(conn, app_id: int, locale: str | None = None):
    params = [app_id]
    extra = ""
    if locale:
        extra = " and locale=%s"
        params.append(locale)
    rows = conn.execute(
        f"""select c.changed_at,c.locale,c.field,c.before_value,c.after_value,
                   coalesce(m.improved,0),coalesce(m.declined,0),coalesce(m.unchanged,0)
            from aso_listing_changes c
            left join lateral (
                with before_positions as (
                    select keyword,avg(average_position) position
                    from aso_keyword_daily
                    where app_id=c.app_id
                      and date >= c.changed_at::date-7 and date < c.changed_at::date
                      and average_position is not null group by keyword
                ), after_positions as (
                    select keyword,avg(average_position) position
                    from aso_keyword_daily
                    where app_id=c.app_id
                      and date >= c.changed_at::date and date < c.changed_at::date+7
                      and average_position is not null group by keyword
                )
                select count(*) filter (where a.position < b.position)::int improved,
                       count(*) filter (where a.position > b.position)::int declined,
                       count(*) filter (where a.position = b.position)::int unchanged
                from before_positions b join after_positions a using (keyword)
            ) m on true
            where c.app_id=%s {extra}
            order by c.changed_at desc,c.id desc""",
        params,
    ).fetchall()
    return tuple(ListingChangeRow(*row) for row in rows)


def current_listings(conn, app_id: int):
    return conn.execute(
        """select distinct on (locale) locale,captured_at,listing
           from aso_listing_snapshots where app_id=%s
           order by locale,captured_at desc,id desc""",
        (app_id,),
    ).fetchall()


def keyword_research(conn, app_id: int, search: str | None = None):
    params = [app_id, app_id, app_id]
    extra = ""
    if search:
        extra = "where p.keyword ilike %s"
        params.append(f"%{search}%")
    rows = conn.execute(
        f"""
        with listing as (
            select string_agg(listing::text,' ') body
            from aso_listing_snapshots
            where app_id=%s and id in (
                select max(id) from aso_listing_snapshots
                where app_id=%s group by locale
            )
        )
        select p.keyword,p.source,p.first_seen_at,p.last_seen_at,
               coalesce(listing.body ilike '%%' || p.keyword || '%%',false),
               exists(select 1 from aso_keyword_daily k
                      where k.app_id=%s and k.keyword=p.keyword)
        from aso_popular_keywords p cross join listing {extra}
        order by 5 desc,6 desc,p.last_seen_at desc,p.keyword
        """,
        params,
    ).fetchall()
    return tuple(ResearchRow(*row) for row in rows)
