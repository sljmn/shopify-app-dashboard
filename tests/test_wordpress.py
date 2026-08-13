import json
from types import SimpleNamespace

import httpx

from app_dashboard.wordpress import WordPressClient, gutenberg_html


def settings():
    return SimpleNamespace(wordpress_configured=True,wordpress_timeout_seconds=10,wordpress_site_url="https://wp.test",wordpress_username="user",wordpress_application_password="pass",wordpress_post_type="marketing-post")


def test_post_payload_and_media_metadata_are_explicit():
    seen=[]
    def handler(request):
        seen.append((request.url.path,request.content,request.headers.get("content-type")))
        if request.url.path=="/wp-json/wp/v2/media": return httpx.Response(201,json={"id":42},request=request)
        if request.url.path=="/wp-json/wp/v2/media/42": return httpx.Response(200,json={"id":42},request=request)
        return httpx.Response(201,json={"id":7,"link":"https://wp.test/post","status":"draft"},request=request)
    wp=WordPressClient(settings(),client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert wp.upload_media(b"png",filename="hero.png",mime_type="image/png",alt_text="Book import") == 42
    post=wp.save_post({"title":"Title","slug":"title","excerpt":"Excerpt","content":"Body","status":"draft","featured_media":42})
    body=json.loads(seen[-1][1])
    assert post.post_id==7 and body["status"]=="draft" and body["featured_media"]==42


def test_gutenberg_renderer_escapes_html():
    rendered=gutenberg_html("## Safe <title>\n\nNo <script> here")
    assert "&lt;title&gt;" in rendered and "&lt;script&gt;" in rendered


def test_default_client_identifies_mantle(monkeypatch):
    captured={}
    class Client:
        def __init__(self, **kwargs): captured.update(kwargs)
    monkeypatch.setattr(httpx,"Client",Client)
    WordPressClient(settings())
    assert captured["headers"]["User-Agent"] == "Newcraft-Mantle-Content/1.0"
