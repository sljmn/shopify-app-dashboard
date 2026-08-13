"""Verified product context used by Content Studio."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from urllib.parse import urlparse

from psycopg.types.json import Jsonb

LANGUAGE_RE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")


class ContentProfileError(ValueError):
    pass


def _lines(raw: str | None) -> list[str]:
    return list(dict.fromkeys(line.strip() for line in (raw or "").splitlines() if line.strip()))


def _url(value: str | None, label: str) -> str | None:
    value = (value or "").strip() or None
    if value and (urlparse(value).scheme != "https" or not urlparse(value).hostname):
        raise ContentProfileError(f"{label} must be an HTTPS URL")
    return value


def _facts(raw: str | None) -> list[dict[str, str]]:
    result = []
    labels = set()
    for line in _lines(raw):
        if ":" not in line:
            raise ContentProfileError("Facts must use Label: verified fact")
        label, value = (part.strip() for part in line.split(":", 1))
        if not label or not value or label.casefold() in labels:
            raise ContentProfileError("Fact labels must be unique and non-empty")
        labels.add(label.casefold())
        result.append({"label": label, "value": value})
    return result


def get_content_profile(conn, app_id: int) -> dict | None:
    row = conn.execute(
        """select p.app_id,p.pillar_url,p.shopify_listing_url,p.wordpress_related_app_id,
                  p.default_language,p.supported_languages,p.facts,p.allowed_claims,
                  p.forbidden_claims,p.audiences,p.objections,p.source_urls,
                  p.style_profile_id,s.name
           from app_content_profiles p left join content_style_profiles s on s.id=p.style_profile_id
           where p.app_id=%s""", (app_id,),
    ).fetchone()
    if not row:
        return None
    keys = ("app_id","pillar_url","shopify_listing_url","wordpress_related_app_id",
            "default_language","supported_languages","facts","allowed_claims",
            "forbidden_claims","audiences","objections","source_urls",
            "style_profile_id","style_profile_name")
    return dict(zip(keys, row, strict=True))


def list_style_profiles(conn) -> list[dict]:
    return [dict(zip(("id","name","version","palette"), row, strict=True)) for row in conn.execute(
        "select id,name,version,palette from content_style_profiles order by name,version desc"
    ).fetchall()]


def save_content_profile(conn, app_id: int, data: Mapping[str, str]) -> None:
    languages = [item.strip() for item in re.split(r"[,\n]", data.get("supported_languages", "en")) if item.strip()]
    if not languages or len(languages) != len(set(languages)) or any(not LANGUAGE_RE.fullmatch(x) for x in languages):
        raise ContentProfileError("Languages must be unique tags such as en or nl-NL")
    default_language = (data.get("default_language") or languages[0]).strip()
    if default_language not in languages:
        raise ContentProfileError("Default language must be supported")
    source_urls = _lines(data.get("source_urls"))
    for source in source_urls:
        _url(source, "Source")
    try:
        wp_id = int(data["wordpress_related_app_id"]) if data.get("wordpress_related_app_id", "").strip() else None
        style_id = int(data["style_profile_id"]) if data.get("style_profile_id", "").strip() else None
    except ValueError as exc:
        raise ContentProfileError("WordPress and style IDs must be numeric") from exc
    values = (
        app_id, _url(data.get("pillar_url"), "Pillar URL"),
        _url(data.get("shopify_listing_url"), "Shopify listing URL"), wp_id,
        default_language, Jsonb(languages), Jsonb(_facts(data.get("facts"))),
        Jsonb(_lines(data.get("allowed_claims"))), Jsonb(_lines(data.get("forbidden_claims"))),
        Jsonb(_lines(data.get("audiences"))), Jsonb(_lines(data.get("objections"))),
        Jsonb(source_urls), style_id,
    )
    conn.execute(
        """insert into app_content_profiles
             (app_id,pillar_url,shopify_listing_url,wordpress_related_app_id,default_language,
              supported_languages,facts,allowed_claims,forbidden_claims,audiences,objections,
              source_urls,style_profile_id)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           on conflict (app_id) do update set pillar_url=excluded.pillar_url,
             shopify_listing_url=excluded.shopify_listing_url,
             wordpress_related_app_id=excluded.wordpress_related_app_id,
             default_language=excluded.default_language,supported_languages=excluded.supported_languages,
             facts=excluded.facts,allowed_claims=excluded.allowed_claims,
             forbidden_claims=excluded.forbidden_claims,audiences=excluded.audiences,
             objections=excluded.objections,source_urls=excluded.source_urls,
             style_profile_id=excluded.style_profile_id,updated_at=now()""", values,
    )


def profile_form_values(profile: dict | None) -> dict:
    if not profile:
        return {"default_language":"en","supported_languages":"en"}
    value = dict(profile)
    value["supported_languages"] = ", ".join(profile["supported_languages"])
    value["facts"] = "\n".join(f'{fact["label"]}: {fact["value"]}' for fact in profile["facts"])
    for key in ("allowed_claims","forbidden_claims","audiences","objections","source_urls"):
        value[key] = "\n".join(profile[key])
    return value
