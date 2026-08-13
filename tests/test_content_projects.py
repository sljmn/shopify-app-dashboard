from app_dashboard.content_profiles import save_content_profile
from app_dashboard.content_projects import add_version, agent_brief, create_project, project_detail


def test_project_versions_are_immutable_and_brief_uses_facts(db, test_app):
    save_content_profile(db,test_app.id,{"supported_languages":"en","facts":"Import: Creates a Shopify product"})
    project_id=create_project(db,app_id=test_app.id,title="ISBN import guide",target_query="import books shopify",channel="seo_article",language="en",author="tester")
    add_version(db,project_id,"brief",payload={"angle":"workflow"},text="Brief one",author="tester",accept=True)
    add_version(db,project_id,"brief",payload={"angle":"cost"},text="Brief two",author="tester",accept=True)
    project=project_detail(db,project_id)
    assert [v["version_number"] for v in project["versions"]] == [2,1]
    profile={"facts":[{"label":"Import","value":"Creates a Shopify product"}],"forbidden_claims":[]}
    assert "Creates a Shopify product" in agent_brief(project,profile)
