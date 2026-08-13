import base64
from types import SimpleNamespace

import httpx

from app_dashboard.content_media import build_image_prompt, generate_image


PNG=b"\x89PNG\r\n\x1a\nsmall-test-image"


class FakeStore:
    def upload_image(self,data,*,mime_type):
        assert data==PNG and mime_type=="image/png"
        return SimpleNamespace(digest="abc",object_key="content/ab/abc",mime_type=mime_type,byte_size=len(data))


def test_prompt_enforces_editorial_style_without_text():
    prompt=build_image_prompt(
        {"title":"Guide","target_query":"book import","app_name":"Books"},{},
        {"prompt_template":"Illustrate {subject}","palette":"green, ochre","rules":{"avoid":["logos"]}},
    )
    assert "No text in the image" in prompt and "green, ochre" in prompt


def test_generated_image_is_persisted(db,test_app):
    style_id=db.execute("insert into content_style_profiles (name,prompt_template) values ('Editorial','Illustrate {subject}') returning id").fetchone()[0]
    db.execute("insert into app_content_profiles (app_id,style_profile_id) values (%s,%s)",(test_app.id,style_id))
    project_id=db.execute("insert into content_projects (app_id,title,target_query,channel,language,author) values (%s,'Guide','book import','seo_article','en','tester') returning id",(test_app.id,)).fetchone()[0]
    project={"id":project_id,"title":"Guide","target_query":"book import","app_name":"Books"}
    settings=SimpleNamespace(openrouter_configured=True,b2_configured=True,openrouter_timeout_seconds=10,openrouter_api_key="secret",public_base_url="https://mantle.test",openrouter_image_model="image/test")
    def handler(request):
        return httpx.Response(200,json={"data":[{"b64_json":base64.b64encode(PNG).decode()}],"usage":{"cost":1}},request=request)
    result=generate_image(db,settings,project,{"style_profile_id":style_id},client=httpx.Client(transport=httpx.MockTransport(handler)),store=FakeStore())
    assert result.object_key=="content/ab/abc"
    assert db.execute("select selected from content_media where id=%s",(result.media_id,)).fetchone()[0] is True
