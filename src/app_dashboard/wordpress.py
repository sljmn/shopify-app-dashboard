"""Typed WordPress REST publishing boundary."""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass

import httpx


class WordPressError(RuntimeError): pass

@dataclass(frozen=True)
class PublishedPost:
    post_id: int
    url: str
    status: str
    payload_hash: str


class WordPressClient:
    def __init__(self,settings,*,client=None):
        if not settings.wordpress_configured: raise WordPressError("WordPress is not configured")
        self.settings=settings
        self.client=client or httpx.Client(
            timeout=settings.wordpress_timeout_seconds,
            auth=(settings.wordpress_username,settings.wordpress_application_password),
            headers={"User-Agent":"Newcraft-Mantle-Content/1.0"},
        )

    def test(self) -> bool:
        response=self.client.get(f"{self.settings.wordpress_site_url.rstrip('/')}/wp-json/wp/v2/users/me?context=edit")
        if response.status_code>=400: raise WordPressError(f"WordPress connection failed ({response.status_code})")
        return True

    def save_post(self,payload: dict,*,post_id: int|None=None) -> PublishedPost:
        root=f"{self.settings.wordpress_site_url.rstrip('/')}/wp-json/wp/v2/{self.settings.wordpress_post_type}"
        body={"title":payload["title"],"slug":payload["slug"],"excerpt":payload["excerpt"],"content":payload["content"],"status":payload.get("status","draft")}
        if payload.get("date"): body["date"]=payload["date"]
        if payload.get("related_app_id"): body["acf"]={"related_apps":[payload["related_app_id"]]}
        if payload.get("featured_media"): body["featured_media"]=payload["featured_media"]
        digest=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
        response=self.client.post(f"{root}/{post_id}" if post_id else root,json=body)
        if response.status_code>=400: raise WordPressError(f"WordPress publish failed ({response.status_code})")
        data=response.json()
        return PublishedPost(int(data["id"]),data.get("link","") ,data.get("status",body["status"]),digest)

    def upload_media(self, data: bytes, *, filename: str, mime_type: str, alt_text: str) -> int:
        root=f"{self.settings.wordpress_site_url.rstrip('/')}/wp-json/wp/v2/media"
        response=self.client.post(
            root, content=data,
            headers={"Content-Type":mime_type,"Content-Disposition":f'attachment; filename="{filename}"'},
        )
        if response.status_code>=400: raise WordPressError(f"WordPress media upload failed ({response.status_code})")
        media_id=int(response.json()["id"])
        updated=self.client.post(f"{root}/{media_id}",json={"alt_text":alt_text})
        if updated.status_code>=400: raise WordPressError(f"WordPress media metadata failed ({updated.status_code})")
        return media_id


def gutenberg_html(text: str) -> str:
    blocks=[]
    for paragraph in [p.strip() for p in text.split("\n\n") if p.strip()]:
        if paragraph.startswith("## "):
            blocks.append(f'<!-- wp:heading --><h2 class="wp-block-heading">{html.escape(paragraph[3:])}</h2><!-- /wp:heading -->')
        else:
            blocks.append(f"<!-- wp:paragraph --><p>{html.escape(paragraph)}</p><!-- /wp:paragraph -->")
    return "\n".join(blocks)
