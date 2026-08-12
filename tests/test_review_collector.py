from datetime import UTC, date, datetime

from app_dashboard.app_store_discovery import SitemapApp, sync_discovered_apps
from app_dashboard.discovery_watchlist import follow_app, unfollow_app
from app_dashboard.review_collector import (
    parse_review_page,
    review_report,
    review_sync_targets,
    sync_app_reviews,
)


def review_html(*, reply=True, next_page=True):
    reply_html = """
      <div id="review-reply-99">
        <div class="tw-text-body-xs tw-text-fg-tertiary tw-mb-sm">
          Demo Dev replied March 25, 2026
        </div>
        <div data-truncate-review>
          <div data-truncate-content-copy><p>Thanks for the feedback.</p></div>
        </div>
      </div>
    """ if reply else ""
    next_html = '<a rel="next" href="?sort_by=newest&amp;page=2">Next</a>' \
        if next_page else ""
    return f"""
      <div id="review-867123">
        <div data-merchant-review data-review-content-id="867123">
          <div class="tw-order-2">
            <div>
              <div aria-label="4 out of 5 stars" role="img"></div>
              <div class="tw-text-body-xs tw-text-fg-tertiary">March 21, 2026</div>
            </div>
            <div data-truncate-review>
              <div data-truncate-content-copy><p>Useful app with clear setup.</p></div>
            </div>
          </div>
          <div class="tw-order-1 lg:tw-row-span-2">
            <span title="Book House">Book House</span>
            <button data-review-id="867123"></button>
            <div>Netherlands</div>
            <div>About 2 months using the app</div>
          </div>
          {reply_html}
        </div>
      </div>
      {next_html}
    """


def test_parse_review_page_captures_review_and_developer_reply():
    page = parse_review_page(review_html(), "alpha")
    assert page.has_next is True
    assert len(page.reviews) == 1
    review = page.reviews[0]
    assert review.shopify_review_id == 867123
    assert review.rating == 4
    assert review.reviewed_on == date(2026, 3, 21)
    assert review.merchant_name == "Book House"
    assert review.country == "Netherlands"
    assert review.usage_duration == "About 2 months using the app"
    assert review.body == "Useful app with clear setup."
    assert review.developer_reply == "Thanks for the feedback."
    assert review.developer_replied_on == date(2026, 3, 25)
    assert review.source_url.endswith("/reviews/867123")


def test_parse_review_page_handles_missing_reply_and_final_page():
    page = parse_review_page(review_html(reply=False, next_page=False), "alpha")
    assert page.has_next is False
    assert page.reviews[0].developer_reply is None
    assert page.reviews[0].developer_replied_on is None


def page_html(review_id, *, next_page):
    return review_html(reply=False, next_page=next_page).replace(
        "867123", str(review_id)
    )


class Response:
    status_code = 200

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_review_backfill_resumes_and_upserts_without_duplicates(db):
    now = datetime(2026, 8, 12, 8, tzinfo=UTC)
    sync_discovered_apps(db, [SitemapApp("alpha", None)], now)
    follow_app(db, "alpha", source="manual", now=now)
    app_id = db.execute(
        "select id from discovered_apps where handle='alpha'"
    ).fetchone()[0]
    pages = {
        1: page_html(103, next_page=True),
        2: page_html(102, next_page=True),
        3: page_html(101, next_page=False),
    }
    calls = []

    def get(url, **kwargs):
        page = int(url.rsplit("page=", 1)[1])
        calls.append(page)
        return Response(pages[page])

    first = sync_app_reviews(
        db, app_id, "alpha", http_get=get, now=now,
        max_backfill_pages=2, sleep=lambda *_: None,
    )
    assert first == {
        "handle": "alpha", "ok": True, "captured": 2,
        "backfill_complete": False, "next_backfill_page": 3,
    }
    assert calls == [1, 2]

    calls.clear()
    second = sync_app_reviews(
        db, app_id, "alpha", http_get=get, now=now,
        max_backfill_pages=2, sleep=lambda *_: None,
    )
    assert second["captured"] == 1
    assert second["backfill_complete"] is True
    assert calls == [1, 3]
    assert db.execute("select count(*) from discovery_reviews").fetchone()[0] == 3

    calls.clear()
    third = sync_app_reviews(
        db, app_id, "alpha", http_get=get, now=now,
        sleep=lambda *_: None,
    )
    assert third["captured"] == 0
    assert calls == [1]
    assert db.execute("select count(*) from discovery_reviews").fetchone()[0] == 3


def test_review_targets_keep_active_apps_and_incomplete_new_app_backfills(db):
    now = datetime(2026, 8, 12, 8, tzinfo=UTC)
    sync_discovered_apps(db, [SitemapApp("baseline", None)], now)
    sync_discovered_apps(db, [
        SitemapApp("baseline", None), SitemapApp("new-app", None),
    ], now)
    follow_app(db, "baseline", source="manual", now=now)
    new_id = db.execute(
        "select id from discovered_apps where handle='new-app'"
    ).fetchone()[0]
    unfollow_app(db, "new-app", now=now)
    targets = review_sync_targets(db)
    assert {handle for _, handle in targets} == {"baseline", "new-app"}

    db.execute(
        """insert into discovery_review_sync_state
             (discovered_app_id,next_backfill_page,backfill_completed_at)
           values (%s,1,%s)""",
        (new_id, now),
    )
    assert review_sync_targets(db) == [
        (db.execute(
            "select id from discovered_apps where handle='baseline'"
        ).fetchone()[0], "baseline")
    ]


def test_review_report_filters_and_exposes_backfill_state(db):
    now = datetime(2026, 8, 12, 8, tzinfo=UTC)
    sync_discovered_apps(db, [SitemapApp("alpha", None)], now)
    app_id = db.execute(
        "select id from discovered_apps where handle='alpha'"
    ).fetchone()[0]

    def get(url, **kwargs):
        return Response(review_html(next_page=False))

    sync_app_reviews(db, app_id, "alpha", http_get=get, now=now)
    report = review_report(db, app_id, rating=4)
    assert report["captured"] == 1
    assert report["developer_replies"] == 1
    assert report["distribution"][4] == 1
    assert report["filtered_total"] == 1
    assert report["rows"][0]["merchant_name"] == "Book House"
    assert report["state"]["backfill_completed_at"] == now
