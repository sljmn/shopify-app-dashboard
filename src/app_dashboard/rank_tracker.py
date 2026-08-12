"""CRUD and reports for manually selected Shopify keyword rankings."""

LOCALES = {
    "en": "English",
    "nl": "Dutch",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt-BR": "Portuguese (Brazil)",
    "sv": "Swedish",
    "da": "Danish",
    "fi": "Finnish",
    "pl": "Polish",
}


def create_rank_list(conn, name):
    name = " ".join(name.split())
    if not name:
        raise ValueError("name-required")
    return conn.execute(
        "insert into aso_rank_lists(name) values(%s) returning id", (name,)
    ).fetchone()[0]


def add_rank_keyword(conn, list_id, keyword, locale, country=""):
    keyword = " ".join(keyword.split()).casefold()
    locale = locale.strip()
    country = country.strip().upper()
    if not keyword:
        raise ValueError("keyword-required")
    if locale not in LOCALES:
        raise ValueError("invalid-locale")
    row = conn.execute(
        """insert into aso_rank_keywords(rank_list_id, keyword, locale, country)
           values(%s, %s, %s, %s)
           on conflict do nothing returning id""",
        (list_id, keyword, locale, country),
    ).fetchone()
    return row[0] if row else None


def archive_rank_keyword(conn, keyword_id):
    conn.execute(
        "update aso_rank_keywords set active=false, updated_at=now() where id=%s",
        (keyword_id,),
    )


def rank_lists(conn):
    rows = conn.execute(
        """select l.id, l.name, l.status,
                  count(k.id) filter (where k.active),
                  max(k.last_scanned_at),
                  count(k.id) filter (where k.active and k.last_scan_error is not null)
           from aso_rank_lists l
           left join aso_rank_keywords k on k.rank_list_id=l.id
           group by l.id order by l.updated_at desc"""
    ).fetchall()
    keys = ("id", "name", "status", "keywords", "last_scanned_at", "failures")
    return [dict(zip(keys, row, strict=True)) for row in rows]


def rank_list_detail(conn, list_id):
    row = conn.execute(
        "select id, name, status from aso_rank_lists where id=%s", (list_id,)
    ).fetchone()
    if not row:
        return None
    keyword_rows = conn.execute(
        """select k.id, k.keyword, k.locale, k.country, k.last_scanned_at,
                  k.last_scan_error, coalesce(s.result_count, 0)
           from aso_rank_keywords k
           left join lateral (
               select result_count from aso_rank_scans
               where rank_keyword_id=k.id order by captured_on desc limit 1
           ) s on true
           where k.rank_list_id=%s and k.active
           order by k.keyword""",
        (list_id,),
    ).fetchall()
    keys = ("id", "keyword", "locale", "country", "last_scanned_at", "error", "results")
    return {
        "id": row[0],
        "name": row[1],
        "status": row[2],
        "keywords": [dict(zip(keys, item, strict=True)) for item in keyword_rows],
    }


def keyword_detail(conn, keyword_id):
    head = conn.execute(
        """select k.id, k.keyword, k.locale, k.country, k.rank_list_id, l.name,
                  k.last_scanned_at, k.last_scan_error
           from aso_rank_keywords k join aso_rank_lists l on l.id=k.rank_list_id
           where k.id=%s and k.active""",
        (keyword_id,),
    ).fetchone()
    if not head:
        return None
    rows = conn.execute(
        """with latest as (
               select id, captured_on from aso_rank_scans
               where rank_keyword_id=%s order by captured_on desc limit 1
           ), prior as (
               select v.days, s.id
               from (values (1), (7), (30)) v(days)
               left join lateral (
                   select id from aso_rank_scans
                   where rank_keyword_id=%s
                     and captured_on <= (select captured_on from latest) - v.days
                   order by captured_on desc limit 1
               ) s on true
           )
           select r.position, a.handle, r.display_name, r.review_count, r.rating,
                  r.built_for_shopify, p1.position, p7.position, p30.position
           from latest join aso_rank_results r on r.rank_scan_id=latest.id
           join discovered_apps a on a.id=r.discovered_app_id
           left join aso_rank_results p1 on p1.rank_scan_id=(select id from prior where days=1)
               and p1.discovered_app_id=r.discovered_app_id
           left join aso_rank_results p7 on p7.rank_scan_id=(select id from prior where days=7)
               and p7.discovered_app_id=r.discovered_app_id
           left join aso_rank_results p30 on p30.rank_scan_id=(select id from prior where days=30)
               and p30.discovered_app_id=r.discovered_app_id
           order by r.position""",
        (keyword_id, keyword_id),
    ).fetchall()
    keys = (
        "position",
        "handle",
        "name",
        "reviews",
        "rating",
        "bfs",
        "prior_1",
        "prior_7",
        "prior_30",
    )
    return {
        "id": head[0],
        "keyword": head[1],
        "locale": head[2],
        "country": head[3],
        "list_id": head[4],
        "list_name": head[5],
        "last_scanned_at": head[6],
        "error": head[7],
        "rows": [dict(zip(keys, row, strict=True)) for row in rows],
    }
