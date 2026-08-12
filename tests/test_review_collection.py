import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app_dashboard.review_collection import (
    issue_review_decision, parse_contact, parse_outcome, record_outcome,
    redact_contacts, upsert_contact,
)
from app_dashboard.usage import UsageError, ingest

NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
GID = "gid://shopify/Shop/123"


def review_app(test_app):
    return replace(
        test_app, usage_event_types=frozenset({"book_import_succeeded"}),
        review_prompt_enabled=True, review_trigger_event="book_import_succeeded",
        review_min_success_count=1, review_min_install_hours=24,
        review_retry_days=90, review_annual_cap=3,
    )


def install(db, app, *, hours=48):
    db.execute(
        """insert into shops (app_id, shop_gid, shop_name, install_state, installed_at)
           values (%s,%s,'Books','installed',%s)""",
        (app.id, GID, NOW - timedelta(hours=hours)),
    )
    db.commit()


def event(event_id="event-1"):
    return {"shop_gid": GID, "event_id": event_id,
            "event_type": "book_import_succeeded", "occurred_at": NOW,
            "properties": {}}


def test_contact_capture_keeps_staff_separate_and_omits_unverified_email(db, test_app):
    base = {"shop_gid": GID, "shop_domain": "Example.MyShopify.com", "kind": "staff",
            "first_name": "A", "last_name": "One", "email": "A@EXAMPLE.COM",
            "email_verified": False, "seen_at": NOW.isoformat()}
    first = parse_contact(json.dumps({**base, "shopify_user_id": "1"}).encode())
    second = parse_contact(json.dumps({**base, "shopify_user_id": "2",
                                       "email_verified": True}).encode())
    upsert_contact(db, test_app.id, first)
    upsert_contact(db, test_app.id, second)
    rows = db.execute(
        "select shopify_user_id, email from merchant_contacts order by shopify_user_id"
    ).fetchall()
    assert rows == [("1", None), ("2", "a@example.com")]


def test_contact_payload_rejects_unknown_fields():
    with pytest.raises(UsageError):
        parse_contact(json.dumps({"shop_gid": GID, "kind": "shop",
                                  "seen_at": NOW.isoformat(), "secret": "no"}).encode())


def test_outcome_rejects_a_success_flag_that_disagrees_with_shopify_code():
    with pytest.raises(UsageError):
        parse_outcome(json.dumps({"decision_id": "d", "success": True,
                                  "code": "cancelled", "message": "No"}).encode())


def test_eligible_success_event_issues_only_one_decision(db, test_app):
    app = review_app(test_app)
    install(db, app)
    ingest(db, app.id, [event()])
    decision = issue_review_decision(
        db, app, shop_gid=GID, event_id="event-1",
        event_type="book_import_succeeded", now=NOW,
    )
    assert decision is not None
    assert issue_review_decision(
        db, app, shop_gid=GID, event_id="event-1",
        event_type="book_import_succeeded", now=NOW,
    ) is None


def test_second_success_cannot_issue_while_a_decision_is_active(db, test_app):
    app = review_app(test_app)
    install(db, app)
    ingest(db, app.id, [event("event-1"), event("event-2")])
    assert issue_review_decision(db, app, shop_gid=GID, event_id="event-1",
                                 event_type="book_import_succeeded", now=NOW)
    assert issue_review_decision(db, app, shop_gid=GID, event_id="event-2",
                                 event_type="book_import_succeeded", now=NOW) is None


def test_young_install_and_suppression_block_decision(db, test_app):
    app = review_app(test_app)
    install(db, app, hours=1)
    ingest(db, app.id, [event()])
    assert issue_review_decision(db, app, shop_gid=GID, event_id="event-1",
                                 event_type="book_import_succeeded", now=NOW) is None


def test_confirmed_partner_development_shop_cannot_receive_a_decision(db, test_app):
    app = review_app(test_app)
    install(db, app)
    db.execute(
        "update shops set partner_development=true where app_id=%s and shop_gid=%s",
        (app.id, GID),
    )
    db.commit()
    ingest(db, app.id, [event()])

    assert issue_review_decision(
        db, app, shop_gid=GID, event_id="event-1",
        event_type="book_import_succeeded", now=NOW,
    ) is None


def test_shop_contact_records_authoritative_development_store_status(db, test_app):
    contact = parse_contact(json.dumps({
        "shop_gid": GID,
        "shop_domain": "books-dev.myshopify.com",
        "kind": "shop",
        "email": "owner@example.com",
        "email_verified": True,
        "partner_development": True,
        "seen_at": NOW.isoformat(),
    }).encode())

    upsert_contact(db, test_app.id, contact)

    assert db.execute(
        """select partner_development, shop_domain from shops
           where app_id=%s and shop_gid=%s""",
        (test_app.id, GID),
    ).fetchone() == (True, "books-dev.myshopify.com")


def test_outcome_is_idempotent_and_schedules_cancelled_retry(db, test_app):
    app = review_app(test_app)
    install(db, app)
    ingest(db, app.id, [event()])
    decision = issue_review_decision(db, app, shop_gid=GID, event_id="event-1",
                                     event_type="book_import_succeeded", now=NOW)
    outcome = parse_outcome(json.dumps({"decision_id": decision.decision_id,
        "success": False, "code": "cancelled", "message": "No thanks"}).encode())
    assert record_outcome(db, app.id, outcome, NOW) == "cancelled"
    assert record_outcome(db, app.id, outcome, NOW) == "cancelled"
    assert db.execute("select next_eligible_at from review_prompt_decisions").fetchone()[0] == NOW + timedelta(days=90)


def test_cancelled_retry_uses_the_app_setting(db, test_app):
    app = replace(review_app(test_app), review_retry_days=21)
    db.execute("update apps set review_retry_days=21 where id=%s", (app.id,))
    install(db, app)
    ingest(db, app.id, [event()])
    decision = issue_review_decision(db, app, shop_gid=GID, event_id="event-1",
                                     event_type="book_import_succeeded", now=NOW)
    outcome = parse_outcome(json.dumps({"decision_id": decision.decision_id,
        "success": False, "code": "cancelled", "message": "Later"}).encode())
    record_outcome(db, app.id, outcome, NOW)
    assert db.execute("select next_eligible_at from review_prompt_decisions").fetchone()[0] == NOW + timedelta(days=21)


def test_redaction_removes_contacts_but_keeps_decision(db, test_app):
    contact = parse_contact(json.dumps({"shop_gid": GID, "kind": "shop",
        "email": "shop@example.com", "email_verified": True,
        "seen_at": NOW.isoformat()}).encode())
    upsert_contact(db, test_app.id, contact)
    db.execute("""insert into review_prompt_decisions
        (decision_id, app_id, shop_gid, event_id, event_type, issued_at, expires_at)
        values ('d',%s,%s,'e','x',%s,%s)""",
        (test_app.id, GID, NOW, NOW + timedelta(minutes=15)))
    db.commit()
    assert redact_contacts(db, test_app.id, GID) == 1
    assert db.execute("select count(*) from merchant_contacts").fetchone()[0] == 0
    assert db.execute("select count(*) from review_prompt_decisions").fetchone()[0] == 1
