"""Authenticated Content Studio browser routes."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from app_dashboard.content_ai import run_stage
from app_dashboard.content_inventory import overlap_candidates, sync_inventory
from app_dashboard.content_media import generate_image
from app_dashboard.content_profiles import ContentProfileError, get_content_profile, list_style_profiles, profile_form_values, save_content_profile
from app_dashboard.content_projects import ContentProjectError, add_version, agent_brief, create_project, list_projects, project_detail
from app_dashboard.content_quality import publication_ready, run_checks
from app_dashboard.wordpress import WordPressClient, WordPressError, gutenberg_html
from app_dashboard.object_storage import ContentObjectStore


def build_content_router(*, conn_factory, settings, templates, verify_creds, page_context, active_apps, browser_form, flat_form):
    router=APIRouter()

    def context(request,user,conn,extra=None):
        apps=active_apps(conn); base=page_context(request,user,"content",None,apps)
        base.update(extra or {}); return base

    @router.get("/content")
    def index(request:Request,q:str="",stage:str="",user:str=Depends(verify_creds)):
        conn=conn_factory()
        try:
            rows=list_projects(conn,query=q,stage=stage)
            inventory=conn.execute("select count(*),max(last_seen_at) from content_inventory where removed_at is null").fetchone()
            configured_profile_ids = {
                row[0] for row in conn.execute("select app_id from app_content_profiles").fetchall()
            }
            return templates.TemplateResponse(request,"content_index.html",context(request,user,conn,{"projects":rows,"q":q,"stage":stage,"inventory_count":inventory[0],"inventory_synced_at":inventory[1],"configured_profile_ids":configured_profile_ids,"openrouter_configured":settings.openrouter_configured,"wordpress_configured":settings.wordpress_configured}))
        finally: conn.close()

    @router.get("/content/new")
    def new(request:Request,user:str=Depends(verify_creds)):
        conn=conn_factory()
        try:return templates.TemplateResponse(request,"content_form.html",context(request,user,conn,{"profiles":{r[0]:r[1] for r in conn.execute("select app_id,default_language from app_content_profiles").fetchall()},"error":None,"values":{}}))
        finally:conn.close()

    @router.post("/content")
    async def create(request:Request,user:str=Depends(verify_creds)):
        _,raw=await browser_form(request); values=flat_form(raw); conn=conn_factory()
        try:
            project_id=create_project(conn,app_id=int(values.get("app_id","0")),title=values.get("title",""),target_query=values.get("target_query",""),channel=values.get("channel","seo_article"),language=values.get("language","en"),author=user)
            return RedirectResponse(f"/content/{project_id}",303)
        except (ValueError,ContentProjectError) as exc:
            return templates.TemplateResponse(request,"content_form.html",context(request,user,conn,{"profiles":{r[0]:r[1] for r in conn.execute("select app_id,default_language from app_content_profiles").fetchall()},"error":str(exc),"values":values}),status_code=422)
        finally:conn.close()

    @router.post("/content/inventory/sync")
    async def sync(request:Request,user:str=Depends(verify_creds)):
        await browser_form(request); conn=conn_factory()
        try:
            try: result=sync_inventory(conn,settings); message=f"Indexed {result['pages']} pages"
            except Exception as exc: message=f"Sync failed: {str(exc)[:120]}"
            return RedirectResponse("/content?"+urlencode({"notice":message}),303)
        finally:conn.close()

    @router.get("/content/profiles/{app_id}")
    def edit_profile(request:Request,app_id:int,user:str=Depends(verify_creds)):
        conn=conn_factory()
        try:
            app=conn.execute("select id,name,slug from apps where id=%s",(app_id,)).fetchone()
            if not app: raise HTTPException(404,"Unknown app")
            return templates.TemplateResponse(request,"content_profile.html",context(request,user,conn,{"profile_app":{"id":app[0],"name":app[1],"slug":app[2]},"values":profile_form_values(get_content_profile(conn,app_id)),"styles":list_style_profiles(conn),"error":None}))
        finally:conn.close()

    @router.post("/content/profiles/{app_id}")
    async def store_profile(request:Request,app_id:int,user:str=Depends(verify_creds)):
        _,raw=await browser_form(request); values=flat_form(raw); conn=conn_factory()
        try:
            try: save_content_profile(conn,app_id,values)
            except ContentProfileError as exc:
                app=conn.execute("select id,name,slug from apps where id=%s",(app_id,)).fetchone()
                return templates.TemplateResponse(request,"content_profile.html",context(request,user,conn,{"profile_app":{"id":app[0],"name":app[1],"slug":app[2]},"values":values,"styles":list_style_profiles(conn),"error":str(exc)}),status_code=422)
            return RedirectResponse(f"/content/profiles/{app_id}?saved=1",303)
        finally:conn.close()

    @router.get("/content/{project_id}")
    def detail(request:Request,project_id:int,user:str=Depends(verify_creds)):
        conn=conn_factory()
        try:
            try: project=project_detail(conn,project_id)
            except KeyError: raise HTTPException(404,"Unknown content project") from None
            profile=get_content_profile(conn,project["app_id"])
            candidates=overlap_candidates(conn,project["target_query"])
            ready,reason=publication_ready(project,project["checks"])
            return templates.TemplateResponse(request,"content_project.html",context(request,user,conn,{"project":project,"profile":profile,"candidates":candidates,"agent_brief":agent_brief(project,profile),"publication_ready":ready,"publication_reason":reason,"openrouter_configured":settings.openrouter_configured,"image_generation_configured":settings.openrouter_configured and settings.b2_configured,"wordpress_configured":settings.wordpress_configured,"error":request.query_params.get("error"),"notice":request.query_params.get("notice")}))
        finally:conn.close()

    @router.get("/content/{project_id}/agent-brief")
    def brief(project_id:int,user:str=Depends(verify_creds)):
        conn=conn_factory()
        try:
            project=project_detail(conn,project_id); return PlainTextResponse(agent_brief(project,get_content_profile(conn,project["app_id"])),media_type="text/markdown")
        finally:conn.close()

    @router.post("/content/{project_id}/versions")
    async def manual_version(request:Request,project_id:int,user:str=Depends(verify_creds)):
        _,raw=await browser_form(request); values=flat_form(raw); conn=conn_factory()
        try:
            add_version(conn,project_id,values.get("stage","draft"),payload={"manual":True},text=values.get("text",""),author=user,accept=True)
            return RedirectResponse(f"/content/{project_id}?notice=Version+saved",303)
        finally:conn.close()

    @router.post("/content/{project_id}/generate/{stage}")
    async def generate(request:Request,project_id:int,stage:str,user:str=Depends(verify_creds)):
        await browser_form(request)
        if stage not in {"ideas","brief","outline","draft","review"}: raise HTTPException(404,"Unknown generation stage")
        conn=conn_factory()
        try:
            project=project_detail(conn,project_id)
            try: run_stage(conn,settings,project,get_content_profile(conn,project["app_id"]),stage,user)
            except Exception as exc: return RedirectResponse(f"/content/{project_id}?"+urlencode({"error":str(exc)[:160]}),303)
            return RedirectResponse(f"/content/{project_id}?notice={stage.title()}+generated",303)
        finally:conn.close()

    @router.post("/content/{project_id}/overlap")
    async def overlap(request:Request,project_id:int,user:str=Depends(verify_creds)):
        _,raw=await browser_form(request); values=flat_form(raw); status=values.get("overlap_status","")
        if status not in {"clear","differentiate","update_existing","blocked"}: raise HTTPException(422,"Invalid overlap decision")
        conn=conn_factory()
        try:
            conn.execute("update content_projects set overlap_status=%s,overlap_note=%s,updated_at=now() where id=%s",(status,values.get("overlap_note",""),project_id))
            return RedirectResponse(f"/content/{project_id}?notice=Overlap+decision+saved",303)
        finally:conn.close()

    @router.post("/content/{project_id}/checks")
    async def checks(request:Request,project_id:int,user:str=Depends(verify_creds)):
        await browser_form(request); conn=conn_factory()
        try:
            project=project_detail(conn,project_id); draft=next((v for v in project["versions"] if v["stage"]=="draft" and v["accepted"]),None)
            run_checks(conn,project,get_content_profile(conn,project["app_id"]),draft["rendered_text"] if draft else "",draft["id"] if draft else None)
            return RedirectResponse(f"/content/{project_id}?notice=Checks+complete",303)
        finally:conn.close()

    @router.post("/content/{project_id}/image")
    async def image(request:Request,project_id:int,user:str=Depends(verify_creds)):
        await browser_form(request); conn=conn_factory()
        try:
            project=project_detail(conn,project_id)
            try: generate_image(conn,settings,project,get_content_profile(conn,project["app_id"]))
            except Exception as exc: return RedirectResponse(f"/content/{project_id}?"+urlencode({"error":str(exc)[:160]}),303)
            return RedirectResponse(f"/content/{project_id}?notice=Illustration+generated",303)
        finally:conn.close()

    @router.get("/content/media/{media_id}")
    def media(media_id:int,user:str=Depends(verify_creds)):
        conn=conn_factory()
        try:
            row=conn.execute("select object_key from content_media where id=%s",(media_id,)).fetchone()
            if not row: raise HTTPException(404,"Unknown content image")
            return RedirectResponse(ContentObjectStore(settings).presigned_inline(row[0]),302)
        finally:conn.close()

    @router.post("/content/{project_id}/wordpress-draft")
    async def wordpress_draft(request:Request,project_id:int,user:str=Depends(verify_creds)):
        await browser_form(request); conn=conn_factory()
        try:
            _save_wordpress(conn,project_id,"draft")
            return RedirectResponse(f"/content/{project_id}?notice=WordPress+draft+saved",303)
        except WordPressError as exc:return RedirectResponse(f"/content/{project_id}?"+urlencode({"error":str(exc)}),303)
        finally:conn.close()

    @router.post("/content/{project_id}/wordpress-publish")
    async def wordpress_publish(request:Request,project_id:int,user:str=Depends(verify_creds)):
        await browser_form(request); conn=conn_factory()
        try:
            _save_wordpress(conn,project_id,"publish")
            conn.execute("update content_projects set stage='published',updated_at=now() where id=%s",(project_id,))
            return RedirectResponse(f"/content/{project_id}?notice=Published+to+WordPress",303)
        except WordPressError as exc:return RedirectResponse(f"/content/{project_id}?"+urlencode({"error":str(exc)}),303)
        finally:conn.close()

    def _save_wordpress(conn,project_id:int,status:str):
        project=project_detail(conn,project_id); profile=get_content_profile(conn,project["app_id"]); ready,reason=publication_ready(project,project["checks"])
        if not ready: raise WordPressError(reason)
        draft=next((v for v in project["versions"] if v["stage"]=="draft" and v["accepted"]),None)
        if not draft: raise WordPressError("Accept a draft first")
        data=draft["payload"]; wp=WordPressClient(settings); featured_media=None
        selected=next((item for item in project["media"] if item["role"]=="featured" and item["selected"]),None)
        if selected:
            if selected["wordpress_media_id"]: featured_media=selected["wordpress_media_id"]
            else:
                store=ContentObjectStore(settings)
                download=store.client.get_object(Bucket=store.bucket,Key=selected["object_key"])["Body"].read()
                extension={"image/png":"png","image/jpeg":"jpg","image/webp":"webp","image/gif":"gif"}[selected["mime_type"]]
                featured_media=wp.upload_media(download,filename=f"{project['app_slug']}-{project_id}.{extension}",mime_type=selected["mime_type"],alt_text=selected["alt_text"])
                conn.execute("update content_media set wordpress_media_id=%s where id=%s",(featured_media,selected["id"]))
        post=wp.save_post({"title":data.get("title",project["title"]),"slug":project["target_query"].lower().replace(" ","-"),"excerpt":data.get("excerpt",""),"content":gutenberg_html(draft["rendered_text"]),"status":status,"related_app_id":profile.get("wordpress_related_app_id"),"featured_media":featured_media},post_id=project["publication"][0] if project["publication"] else None)
        conn.execute("""insert into content_publications (project_id,wordpress_post_id,wordpress_url,status,payload_hash,response,published_at)
          values (%s,%s,%s,%s,%s,'{}',case when %s='publish' then now() end)
          on conflict (project_id) where status in ('draft','future','publish') do update set
          wordpress_post_id=excluded.wordpress_post_id,wordpress_url=excluded.wordpress_url,status=excluded.status,
          payload_hash=excluded.payload_hash,published_at=excluded.published_at,updated_at=now()""",
          (project_id,post.post_id,post.url,post.status,post.payload_hash,status))
        return post
    return router
