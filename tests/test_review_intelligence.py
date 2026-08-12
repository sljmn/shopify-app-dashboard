from datetime import UTC, date, datetime, timedelta

from app_dashboard.review_intelligence import (
    percentile,
    review_intelligence_report,
    score_candidate,
)


def candidate(**overrides):
    values = {
        "reviews": 20, "recent": 8, "previous": 2, "rating": 4.8,
        "rank": 20, "category_reviews": [3, 10, 20, 200, 1000],
        "category_recent": [0, 1, 2, 8, 30], "category_apps": 5,
        "active_grower_share": 0.8, "top_ten_concentration": 1.0,
        "observation_count": 3, "backfill_complete": True,
        "last_success_at": datetime(2026, 8, 12, tzinfo=UTC),
        "now": datetime(2026, 8, 12, tzinfo=UTC),
    }
    values.update(overrides)
    return score_candidate(**values)


def test_percentile_is_relative_to_category_population():
    assert percentile(20, [3, 10, 20, 200, 1000]) == 0.6
    assert percentile(20, []) == 0


def test_unexpected_grower_is_not_an_established_category_incumbent():
    result = candidate()
    assert result["unexpected"] is True
    assert result["established"] is False
    assert result["gem_score"] > 50
    assert result["active_grower_share"] == 80


def test_same_review_count_can_be_established_in_a_smaller_category():
    result = candidate(
        reviews=20, recent=1, previous=1,
        category_reviews=[1, 2, 4, 8, 20],
        category_recent=[0, 0, 0, 1, 2],
    )
    assert result["established"] is True
    assert result["unexpected"] is False


def test_confidence_exposes_incomplete_backfill():
    complete = candidate()["confidence"]
    partial = candidate(
        observation_count=1, backfill_complete=False, last_success_at=None,
    )["confidence"]
    assert complete == 100
    assert partial < complete


def test_report_exposes_category_relative_rows_and_review_feed(db):
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    app_ids = {}
    for index, (handle, reviews, recent) in enumerate([
        ("small-grower", 20, 8), ("incumbent", 1000, 30),
        ("quiet-one", 3, 0), ("quiet-two", 8, 0), ("steady", 200, 2),
    ]):
        app_id = db.execute(
            """insert into discovered_apps
                 (handle,display_name,first_seen_at,last_seen_at,is_baseline)
               values (%s,%s,%s,%s,false) returning id""",
            (handle, handle.title(), now, now),
        ).fetchone()[0]
        app_ids[handle] = app_id
        db.execute(
            """insert into discovery_app_observations
                 (discovered_app_id,observed_on,review_count,rating,
                  best_category_rank,observed_at)
               values (%s,%s,%s,4.8,%s,%s)""",
            (app_id, now.date(), reviews, index + 1, now),
        )
        for offset in range(recent):
            db.execute(
                """insert into discovery_reviews
                     (discovered_app_id,shopify_review_id,rating,reviewed_on,
                      body,source_url,first_captured_at,last_captured_at)
                   values (%s,%s,5,%s,%s,%s,%s,%s)""",
                (app_id, app_id * 100 + offset, date(2026, 8, 1) + timedelta(days=offset),
                 f"Review {offset}", f"https://apps.shopify.com/reviews/{offset}",
                 now, now),
            )
    category_id = db.execute(
        """insert into discovery_categories (slug,name,observed_at)
           values ('books','Books',%s) returning id""", (now,)
    ).fetchone()[0]
    for app_id in app_ids.values():
        db.execute(
            """insert into discovered_app_categories
                 (discovered_app_id,category_id)
               values (%s,%s)""", (app_id, category_id),
        )
    report = review_intelligence_report(
        db, category="books", preset="unexpected", now=now,
    )
    assert [row["handle"] for row in report["rows"]] == ["small-grower"]
    assert report["rows"][0]["category_name"] == "Books"
    assert report["feed"][0]["app_name"] in {"Incumbent", "Small-Grower"}
    assert report["coverage"]["reviews"] == 40
