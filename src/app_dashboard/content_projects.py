"""Content Studio project and immutable version persistence."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from app_dashboard.content_profiles import LANGUAGE_RE

POLICY_VERSION = "2026-08-13.1"
CHANNELS = {"seo_article", "youtube"}
STAGES = ("idea","brief","outline","draft","review","media","ready","published","archived")


class ContentProjectError(ValueError):
    pass


def create_project(conn, *, app_id: int, title: str, target_query: str, channel: str,
                   language: str, author: str) -> int:
    title, target_query = title.strip(), target_query.strip()
    if not title or not target_query:
        raise ContentProjectError("Title and target query are required")
    if channel not in CHANNELS or not LANGUAGE_RE.fullmatch(language):
        raise ContentProjectError("Invalid channel or language")
    if conn.execute("select 1 from app_content_profiles where app_id=%s", (app_id,)).fetchone() is None:
        raise ContentProjectError("Configure this app's content profile first")
    return conn.execute(
        """insert into content_projects (app_id,title,target_query,channel,language,author)
           values (%s,%s,%s,%s,%s,%s) returning id""",
        (app_id,title,target_query,channel,language,author),
    ).fetchone()[0]


def list_projects(conn, *, query: str = "", stage: str = "", app_id: int | None = None) -> list[dict]:
    rows = conn.execute(
        """select p.id,p.title,p.target_query,p.channel,p.language,p.stage,p.overlap_status,
                  p.updated_at,a.name,a.slug,
                  (select status from content_publications x where x.project_id=p.id order by x.updated_at desc limit 1)
           from content_projects p join apps a on a.id=p.app_id
           where (%s='' or p.title ilike '%%'||%s||'%%' or p.target_query ilike '%%'||%s||'%%')
             and (%s='' or p.stage=%s) and (%s::bigint is null or p.app_id=%s::bigint)
           order by p.updated_at desc""", (query,query,query,stage,stage,app_id,app_id),
    ).fetchall()
    keys=("id","title","target_query","channel","language","stage","overlap_status","updated_at","app_name","app_slug","publication_status")
    return [dict(zip(keys,row,strict=True)) for row in rows]


def project_detail(conn, project_id: int) -> dict:
    row = conn.execute(
        """select p.id,p.app_id,p.title,p.target_query,p.channel,p.language,p.intent,p.stage,
                  p.overlap_status,p.overlap_note,p.update_inventory_id,p.author,p.created_at,p.updated_at,a.name,a.slug
           from content_projects p join apps a on a.id=p.app_id where p.id=%s""", (project_id,),
    ).fetchone()
    if not row: raise KeyError(project_id)
    keys=("id","app_id","title","target_query","channel","language","intent","stage","overlap_status","overlap_note","update_inventory_id","author","created_at","updated_at","app_name","app_slug")
    result=dict(zip(keys,row,strict=True))
    version_keys=("id","stage","version_number","payload","rendered_text","model","policy_version","author","accepted","created_at")
    result["versions"]=[dict(zip(version_keys,v,strict=True)) for v in conn.execute(
        """select id,stage,version_number,payload,rendered_text,model,policy_version,author,accepted,created_at
           from content_versions where project_id=%s order by created_at desc""",(project_id,)).fetchall()]
    result["checks"]=[{"name":x[0],"severity":x[1],"evidence":x[2]} for x in conn.execute(
        "select check_name,severity,evidence from content_quality_checks where project_id=%s order by id",(project_id,)).fetchall()]
    result["runs"]=[{"type":x[0],"status":x[1],"model":x[2],"error":x[3],"started_at":x[4]} for x in conn.execute(
        "select run_type,status,model,safe_error,started_at from content_runs where project_id=%s order by id desc limit 20",(project_id,)).fetchall()]
    media_keys=("id","role","object_key","mime_type","byte_size","alt_text","prompt","model","selected","wordpress_media_id","created_at")
    result["media"]=[dict(zip(media_keys,x,strict=True)) for x in conn.execute(
        """select id,role,object_key,mime_type,byte_size,alt_text,prompt,model,selected,wordpress_media_id,created_at
           from content_media where project_id=%s order by id desc""",(project_id,)).fetchall()]
    result["publication"] = conn.execute("select wordpress_post_id,wordpress_url,status,updated_at from content_publications where project_id=%s order by id desc limit 1",(project_id,)).fetchone()
    return result


def add_version(conn, project_id: int, stage: str, *, payload: dict, text: str, author: str,
                model: str | None = None, accept: bool = False) -> int:
    if stage not in STAGES: raise ContentProjectError("Invalid stage")
    number=conn.execute("select coalesce(max(version_number),0)+1 from content_versions where project_id=%s and stage=%s",(project_id,stage)).fetchone()[0]
    if accept: conn.execute("update content_versions set accepted=false where project_id=%s and stage=%s",(project_id,stage))
    version_id=conn.execute(
        """insert into content_versions (project_id,stage,version_number,payload,rendered_text,model,policy_version,author,accepted)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        (project_id,stage,number,Jsonb(payload),text,model,POLICY_VERSION,author,accept),
    ).fetchone()[0]
    conn.execute("update content_projects set stage=%s,updated_at=now() where id=%s",(stage,project_id))
    return version_id


def accept_version(conn, project_id: int, version_id: int) -> None:
    row=conn.execute("select stage from content_versions where id=%s and project_id=%s",(version_id,project_id)).fetchone()
    if not row: raise ContentProjectError("Version does not belong to project")
    conn.execute("update content_versions set accepted=false where project_id=%s and stage=%s",(project_id,row[0]))
    conn.execute("update content_versions set accepted=true where id=%s",(version_id,))
    conn.execute("update content_projects set stage=%s,updated_at=now() where id=%s",(row[0],project_id))


def agent_brief(project: dict, profile: dict) -> str:
    facts="\n".join(f'- {x["label"]}: {x["value"]}' for x in profile["facts"]) or "- No verified facts supplied"
    claims="\n".join(f"- {x}" for x in profile.get("allowed_claims",[])) or "- No claims beyond verified facts"
    forbidden="\n".join(f"- {x}" for x in profile.get("forbidden_claims",[])) or "- No extra claims beyond evidence"
    audiences="\n".join(f"- {x}" for x in profile.get("audiences",[])) or "- Not specified"
    objections="\n".join(f"- {x}" for x in profile.get("objections",[])) or "- Not specified"
    sources="\n".join(f"- {x}" for x in profile.get("source_urls",[])) or "- No external sources supplied"
    return f"""# Content brief\n\n## Objective\nCreate a {project['channel'].replace('_',' ')} for `{project['target_query']}` in {project['language']}.\n\n## App\n{project['app_name']}\n\n## Audience\n{audiences}\n\n## Objections to answer\n{objections}\n\n## Verified facts\n{facts}\n\n## Allowed claims\n{claims}\n\n## Forbidden claims\n{forbidden}\n\n## Approved sources\n{sources}\n\n## Required destinations\n- Pillar: {profile.get('pillar_url') or 'not configured'}\n- Shopify listing: {profile.get('shopify_listing_url') or 'not configured'}\n\n## Editorial rules\nUse direct, specific language. No generic introduction, invented facts, emoji, em dashes, fake quotes, keyword stuffing, or repeated conclusion. Cite evidence and preserve internal links. Mark any unsupported statement as an unresolved question instead of presenting it as fact.\n\n## Output contract\nReturn JSON with title, excerpt, body, internal_links, evidence_ids, and unresolved_questions.\n\nPolicy: {POLICY_VERSION}\n"""
