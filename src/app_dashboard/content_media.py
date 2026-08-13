"""Editorial image generation and private persistence."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

import httpx
from psycopg.types.json import Jsonb

from app_dashboard.object_storage import ContentObjectStore


class ContentMediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedImage:
    media_id: int
    object_key: str
    mime_type: str


def build_image_prompt(project: dict, profile: dict, style: dict) -> str:
    template = style.get("prompt_template") or "Editorial illustration about {subject}."
    subject = f"{project['title']}; {project['target_query']}; {project['app_name']}"
    try:
        direction = template.format(subject=subject)
    except (KeyError, ValueError) as exc:
        raise ContentMediaError("Style prompt must contain a valid {subject} placeholder") from exc
    rules = style.get("rules") or {}
    avoid = ", ".join(rules.get("avoid", []))
    return (
        f"{direction}\nPalette: {style.get('palette') or 'balanced editorial colors'}.\n"
        f"Composition: {rules.get('composition', 'one clear focal point')}.\n"
        f"Avoid: {avoid or 'text, logos, generic stock imagery'}.\n"
        "Landscape 16:9. No text in the image."
    )


def generate_image(conn, settings, project: dict, profile: dict, *, client=None, store=None) -> GeneratedImage:
    if not settings.openrouter_configured:
        raise ContentMediaError("OpenRouter is not configured")
    if not settings.b2_configured:
        raise ContentMediaError("Backblaze B2 is not configured")
    style_row = conn.execute(
        """select id,name,prompt_template,palette,rules from content_style_profiles
           where id=%s""", (profile.get("style_profile_id"),),
    ).fetchone()
    if not style_row:
        raise ContentMediaError("Configure a content style profile first")
    style = dict(zip(("id", "name", "prompt_template", "palette", "rules"), style_row, strict=True))
    prompt = build_image_prompt(project, profile, style)
    run_id = conn.execute(
        "insert into content_runs (project_id,run_type,status,model) values (%s,'image','running',%s) returning id",
        (project["id"], settings.openrouter_image_model),
    ).fetchone()[0]
    started = time.perf_counter()
    try:
        http = client or httpx.Client(timeout=settings.openrouter_timeout_seconds)
        response = http.post(
            "https://openrouter.ai/api/v1/images",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json", "HTTP-Referer": settings.public_base_url,
                "X-Title": "Mantle Content Studio",
            },
            json={
                "model": settings.openrouter_image_model, "prompt": prompt,
                "size": "1536x1024", "n": 1, "response_format": "b64_json",
            },
        )
        if response.status_code >= 400:
            raise ContentMediaError(f"OpenRouter image request failed ({response.status_code})")
        payload = response.json()
        item = payload["data"][0]
        mime_type = item.get("media_type") or "image/png"
        if mime_type == "image/svg+xml":
            raise ContentMediaError("Vector image output is not supported")
        raw = base64.b64decode(item["b64_json"], validate=True)
        stored = (store or ContentObjectStore(settings)).upload_image(raw, mime_type=mime_type)
        conn.execute("update content_media set selected=false where project_id=%s and role='featured'", (project["id"],))
        media_id = conn.execute(
            """insert into content_media
               (project_id,role,digest,object_key,mime_type,byte_size,alt_text,prompt,model,style_profile_id,selected)
               values (%s,'featured',%s,%s,%s,%s,%s,%s,%s,%s,true)
               on conflict (project_id,digest) do update set role='featured',selected=true,
                 alt_text=excluded.alt_text,prompt=excluded.prompt,model=excluded.model
               returning id""",
            (project["id"], stored.digest, stored.object_key, stored.mime_type,
             stored.byte_size, project["title"], prompt, settings.openrouter_image_model, style["id"]),
        ).fetchone()[0]
        conn.execute(
            "update content_runs set status='succeeded',usage=%s,finished_at=now() where id=%s",
            (Jsonb({**payload.get("usage", {}), "duration_ms": round((time.perf_counter() - started) * 1000)}), run_id),
        )
        return GeneratedImage(media_id, stored.object_key, stored.mime_type)
    except Exception as exc:
        conn.execute(
            "update content_runs set status='failed',safe_error=%s,finished_at=now() where id=%s",
            (str(exc)[:300], run_id),
        )
        raise
