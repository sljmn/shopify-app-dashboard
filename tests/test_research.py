from datetime import datetime, timezone

from app_dashboard.research import (
    add_developer_to_list,
    add_app_to_list,
    attach_object,
    create_list,
    create_note,
    delete_note,
    get_list,
    research_index,
    remove_app_from_list,
    remove_developer_from_list,
    search_apps,
    search_developers,
    target_research,
    update_note,
)


def discovered(db, handle="research-app", name="Research App"):
    return db.execute(
        """insert into discovered_apps
             (handle,display_name,first_seen_at,last_seen_at,is_baseline)
           values (%s,%s,now(),now(),false) returning id""",
        (handle, name),
    ).fetchone()[0]


def test_app_can_join_multiple_lists_and_membership_starts_tracking(db):
    discovered(db)
    first = create_list(db, "Acquisition")
    second = create_list(db, "ASO examples")
    assert add_app_to_list(db, first["id"], "research-app") is True
    assert add_app_to_list(db, first["id"], "research-app") is False
    assert add_app_to_list(db, second["id"], "research-app") is True
    assert len(get_list(db, first["id"])["apps"]) == 1
    assert db.execute("select count(*) from discovery_watchlist").fetchone()[0] == 1
    assert remove_app_from_list(db, first["id"], "research-app") is True
    assert db.execute(
        "select active from discovery_watchlist"
    ).fetchone()[0] is True


def test_app_search_is_bounded_and_marks_existing_list_members(db):
    research_list = create_list(db, "Targets")
    for index in range(10):
        discovered(db, f"content-tool-{index}", f"Content Tool {index}")
    add_app_to_list(db, research_list["id"], "content-tool-0")

    rows = search_apps(db, "content", list_id=research_list["id"], limit=8)

    assert len(rows) == 8
    assert rows[0]["handle"] == "content-tool-0"
    assert rows[0]["in_list"] is True
    assert all(set(row) == {"handle", "name", "categories", "in_list"} for row in rows)
    assert search_apps(db, "  ", list_id=research_list["id"]) == []


def test_developer_can_join_list_and_follow_its_portfolio(db):
    app_ids = [
        discovered(db, "partner-one", "Partner One"),
        discovered(db, "partner-two", "Partner Two"),
    ]
    developer_id = db.execute(
        """insert into discovered_developers (name,shopify_url)
           values ('Partner Labs','https://apps.shopify.com/partners/partner-labs')
           returning id"""
    ).fetchone()[0]
    db.cursor().executemany(
        """insert into discovered_app_developers
             (discovered_app_id,discovered_developer_id) values (%s,%s)""",
        [(app_id, developer_id) for app_id in app_ids],
    )
    research_list = create_list(db, "Partners")

    assert add_developer_to_list(db, research_list["id"], developer_id) is True
    assert add_developer_to_list(db, research_list["id"], developer_id) is False
    detail = get_list(db, research_list["id"])
    assert detail["developer_count"] == 1
    assert detail["developers"][0]["app_count"] == 2
    assert db.execute(
        "select count(*) from discovery_watchlist where active"
    ).fetchone()[0] == 2
    assert remove_developer_from_list(db, research_list["id"], developer_id) is True
    assert get_list(db, research_list["id"])["developer_count"] == 0


def test_developer_search_marks_membership(db):
    developer_id = db.execute(
        """insert into discovered_developers (name,shopify_url)
           values ('HulkApps','https://apps.shopify.com/partners/hulk-code')
           returning id"""
    ).fetchone()[0]
    research_list = create_list(db, "Competitors")
    add_developer_to_list(db, research_list["id"], developer_id)

    assert search_developers(db, "hulk", list_id=research_list["id"]) == [{
        "id": developer_id, "name": "HulkApps",
        "shopify_url": "https://apps.shopify.com/partners/hulk-code",
        "app_count": 0, "in_list": True,
    }]


def test_targeted_notes_and_index_search_include_context_and_filename(db):
    app_id = discovered(db, "contentpilot", "ContentPilot")
    research_list = create_list(db, "AI content tools")
    add_app_to_list(db, research_list["id"], "contentpilot")
    note = create_note(
        db, target_kind="app", target_id=app_id, title="Pricing experiment",
        body="Watch the annual plan", author="sulejman",
        now=datetime(2026, 8, 12, 10, tzinfo=timezone.utc),
    )
    digest = "a" * 64
    attach_object(
        db, note["id"], digest=digest, object_key=f"research/aa/{digest}",
        mime_type="application/pdf", byte_size=20,
        original_filename="pricing-notes.pdf",
    )
    rows = research_index(db, query="pricing")
    assert {row["type"] for row in rows} == {"note", "attachment"}
    assert next(row for row in rows if row["type"] == "note")["context_title"] == "ContentPilot"
    context = target_research(db, target_kind="app", target_id=app_id)
    assert [item["title"] for item in context["lists"]] == ["AI content tools"]
    assert context["notes"][0]["attachment_count"] == 1


def test_deleting_last_note_reference_marks_physical_object_for_deletion(db):
    app_id = discovered(db)
    note = create_note(
        db, target_kind="app", target_id=app_id, title="Evidence", body="",
        author="tester",
    )
    digest = "b" * 64
    attach_object(
        db, note["id"], digest=digest, object_key=f"research/bb/{digest}",
        mime_type="text/plain", byte_size=4, original_filename="note.txt",
    )
    detached = delete_note(db, note["id"])
    assert detached[0].delete_physical is True
    assert db.execute(
        "select count(*) from research_attachment_objects"
    ).fetchone()[0] == 0


def test_note_can_be_updated_without_losing_its_target(db):
    app_id = discovered(db)
    note = create_note(
        db, target_kind="app", target_id=app_id, title="First title",
        body="First body", author="first@example.com",
    )

    updated = update_note(
        db, note["id"], title="Better title", body="Better body",
        now=datetime(2026, 8, 12, 14, tzinfo=timezone.utc),
    )

    assert updated["title"] == "Better title"
    assert updated["body"] == "Better body"
    assert updated["author"] == "first@example.com"
    assert updated["target_kind"] == "app"
    assert updated["target_id"] == app_id
