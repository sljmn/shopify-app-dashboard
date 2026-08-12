from datetime import UTC, datetime
from pathlib import Path

import httpx

from app_dashboard.rank_collector import (
    collect_keyword_results,
    parse_search_page,
    sync_keyword_rankings,
)
from app_dashboard.rank_tracker import (
    add_rank_keyword,
    create_rank_list,
    keyword_detail,
)

FIXTURE = Path(__file__).parent / "fixtures/shopify_keyword_search.html"


class Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def test_search_parser_reads_localized_cards_and_ignores_promos():
    rows = parse_search_page(FIXTURE.read_text())
    assert [(row.handle, row.name, row.position) for row in rows] == [
        ("judgeme", "Judge.me Product Reviews App", 1),
        ("loox", "Loox Product Reviews", 2),
    ]
    assert rows[0].review_count == 43293
    assert str(rows[0].rating) == "5.0"
    assert rows[0].built_for_shopify is True
    assert rows[1].review_count == 8948


def test_collection_encodes_query_and_requests_turbo_frame():
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response(FIXTURE.read_text())

    rows = collect_keyword_results("product reviews", "nl", get, lambda _: None)
    assert len(rows) == 2
    assert "q=product+reviews" in calls[0][0]
    assert "locale=nl" in calls[0][0]
    assert calls[0][1]["headers"]["Turbo-Frame"] == "search_page"


def test_empty_first_page_is_a_failed_collection():
    def get(url, **kwargs):
        return Response("<turbo-frame id='search_page'></turbo-frame>")

    try:
        collect_keyword_results("missing", "en", get, lambda _: None)
    except ValueError as exc:
        assert str(exc) == "empty-search-results"
    else:
        raise AssertionError("empty Shopify response must not become a zero ranking")


def test_scan_persists_top_results_and_movements(db):
    list_id = create_rank_list(db, "Review apps")
    keyword_id = add_rank_keyword(db, list_id, "product reviews", "nl", "NL")

    def get(url, **kwargs):
        return Response(FIXTURE.read_text())

    result = sync_keyword_rankings(
        db,
        keyword_id,
        get,
        lambda _: None,
        datetime(2026, 8, 12, 12, tzinfo=UTC),
    )
    assert result == {"status": "ready", "results": 2}
    detail = keyword_detail(db, keyword_id)
    assert detail["rows"][0]["handle"] == "judgeme"
    assert detail["rows"][0]["position"] == 1
    assert detail["rows"][0]["prior_7"] is None


def test_failed_scan_keeps_previous_success(db):
    list_id = create_rank_list(db, "Tracked")
    keyword_id = add_rank_keyword(db, list_id, "reviews", "en")
    success = lambda url, **kwargs: Response(FIXTURE.read_text())
    sync_keyword_rankings(db, keyword_id, success, lambda _: None)

    def fail(url, **kwargs):
        raise httpx.ConnectError("offline")

    result = sync_keyword_rankings(db, keyword_id, fail, lambda _: None)
    assert result["status"] == "failed"
    assert db.execute("select count(*) from aso_rank_scans").fetchone()[0] == 1
    assert keyword_detail(db, keyword_id)["rows"]
