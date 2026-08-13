"""Strict OpenRouter boundary for staged content generation."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx
from psycopg.types.json import Jsonb

from app_dashboard.content_projects import POLICY_VERSION, add_version, agent_brief


class ContentAIError(RuntimeError): pass

@dataclass(frozen=True)
class Completion:
    payload: dict
    model: str
    request_id: str | None
    usage: dict
    duration_ms: int


STAGE_SCHEMAS={
 "ideas":{"type":"object","properties":{"ideas":{"type":"array","items":{"type":"object","properties":{"title":{"type":"string"},"target_query":{"type":"string"},"intent":{"type":"string"},"why_now":{"type":"string"}},"required":["title","target_query","intent","why_now"],"additionalProperties":False}}},"required":["ideas"],"additionalProperties":False},
 "brief":{"type":"object","properties":{"audience":{"type":"string"},"problem":{"type":"string"},"promise":{"type":"string"},"evidence":{"type":"array","items":{"type":"string"}}},"required":["audience","problem","promise","evidence"],"additionalProperties":False},
 "outline":{"type":"object","properties":{"title":{"type":"string"},"sections":{"type":"array","items":{"type":"string"}}},"required":["title","sections"],"additionalProperties":False},
 "draft":{"type":"object","properties":{"title":{"type":"string"},"excerpt":{"type":"string"},"body":{"type":"string"},"internal_links":{"type":"array","items":{"type":"string"}},"evidence_ids":{"type":"array","items":{"type":"string"}},"unresolved_questions":{"type":"array","items":{"type":"string"}}},"required":["title","excerpt","body","internal_links","evidence_ids","unresolved_questions"],"additionalProperties":False},
 "review":{"type":"object","properties":{"verdict":{"type":"string","enum":["pass","revise"]},"issues":{"type":"array","items":{"type":"string"}},"revision_instructions":{"type":"array","items":{"type":"string"}}},"required":["verdict","issues","revision_instructions"],"additionalProperties":False},
}


class OpenRouterClient:
    def __init__(self, settings, *, client=None):
        if not settings.openrouter_api_key: raise ContentAIError("OpenRouter is not configured")
        self.settings=settings
        self.client=client or httpx.Client(timeout=settings.openrouter_timeout_seconds)

    def complete(self, *, schema_name: str, messages: list[dict], model: str | None = None) -> Completion:
        schema=STAGE_SCHEMAS[schema_name]
        started=time.perf_counter()
        response=self.client.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {self.settings.openrouter_api_key}","Content-Type":"application/json","HTTP-Referer":self.settings.public_base_url,"X-Title":"Mantle Content Studio"},json={"model":model or self.settings.openrouter_generation_model,"messages":messages,"response_format":{"type":"json_schema","json_schema":{"name":schema_name,"strict":True,"schema":schema}},"provider":{"require_parameters":True}})
        if response.status_code >= 400: raise ContentAIError(f"OpenRouter request failed ({response.status_code})")
        data=response.json()
        try: payload=json.loads(data["choices"][0]["message"]["content"])
        except (KeyError,IndexError,TypeError,json.JSONDecodeError) as exc: raise ContentAIError("OpenRouter returned an invalid structured response") from exc
        _validate(schema, payload)
        return Completion(payload,data.get("model",model or self.settings.openrouter_generation_model),response.headers.get("x-request-id"),data.get("usage",{}),round((time.perf_counter()-started)*1000))


def _validate(schema: dict, value, path: str = "response") -> None:
    expected=schema.get("type")
    if expected=="object":
        if not isinstance(value,dict): raise ContentAIError(f"{path} must be an object")
        required=set(schema.get("required",[])); missing=required-set(value)
        if missing: raise ContentAIError(f"{path} is missing: {', '.join(sorted(missing))}")
        if schema.get("additionalProperties") is False:
            extra=set(value)-set(schema.get("properties",{}))
            if extra: raise ContentAIError(f"{path} has unexpected fields: {', '.join(sorted(extra))}")
        for key,item in value.items():
            child=schema.get("properties",{}).get(key)
            if child: _validate(child,item,f"{path}.{key}")
    elif expected=="array":
        if not isinstance(value,list): raise ContentAIError(f"{path} must be an array")
        for index,item in enumerate(value): _validate(schema["items"],item,f"{path}[{index}]")
    elif expected=="string":
        if not isinstance(value,str): raise ContentAIError(f"{path} must be a string")
        if schema.get("enum") and value not in schema["enum"]: raise ContentAIError(f"{path} has an invalid value")


def run_stage(conn, settings, project: dict, profile: dict, stage: str, author: str, *, client=None) -> int:
    schema_name="draft" if stage=="draft" else stage
    run_id=conn.execute("insert into content_runs (project_id,run_type,status,model) values (%s,%s,'running',%s) returning id",(project["id"],stage,settings.openrouter_generation_model)).fetchone()[0]
    try:
        prompt=agent_brief(project,profile)+f"\nProduce the {stage} stage now."
        result=OpenRouterClient(settings,client=client).complete(schema_name=schema_name,messages=[{"role":"system","content":f"Follow editorial policy {POLICY_VERSION}. Use only supplied evidence."},{"role":"user","content":prompt}],model=settings.openrouter_review_model if stage=="review" else None)
        text=result.payload.get("body") or json.dumps(result.payload,ensure_ascii=False,indent=2)
        version_stage="idea" if stage=="ideas" else stage
        version_id=add_version(conn,project["id"],version_stage,payload=result.payload,text=text,author=author,model=result.model,accept=True)
        conn.execute("update content_runs set status='succeeded',request_id=%s,usage=%s,finished_at=now() where id=%s",(result.request_id,Jsonb(result.usage),run_id))
        return version_id
    except Exception as exc:
        conn.execute("update content_runs set status='failed',safe_error=%s,finished_at=now() where id=%s",(str(exc)[:300],run_id))
        raise
