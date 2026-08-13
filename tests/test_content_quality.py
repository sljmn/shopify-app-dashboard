from app_dashboard.content_quality import check_content, publication_ready


def test_publication_is_blocked_until_overlap_and_draft_are_resolved():
    project={"channel":"seo_article","overlap_status":"unchecked"}
    checks=check_content(project,{"pillar_url":"https://newcraft.dev/apps/book-importer"},"")
    ready,reason=publication_ready(project,checks)
    assert ready is False
    assert "Accepted draft" in reason and "Overlap resolved" in reason


def test_specific_clean_draft_passes_blocking_checks():
    project={"channel":"seo_article","overlap_status":"differentiate"}
    pillar="https://newcraft.dev/apps/book-importer"
    text=f"A specific introduction with evidence and {pillar}.\n\nA second useful paragraph."
    checks=check_content(project,{"pillar_url":pillar},text,internal_links=[pillar,"https://newcraft.dev/guides/isbn"])
    assert not [item for item in checks if item["severity"]=="block"]
