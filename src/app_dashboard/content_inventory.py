"""Bounded Newcraft sitemap inventory synchronization."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse, urlunparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb


class InventoryError(RuntimeError): pass


def canonical_url(raw: str) -> str:
    parsed=urlparse(raw.strip())
    return urlunparse((parsed.scheme.lower(),parsed.netloc.lower(),parsed.path.rstrip("/") or "/","","",""))


def parse_sitemap(xml: str) -> list[dict]:
    root=ElementTree.fromstring(xml)
    result=[]
    for node in root.findall(".//{*}url"):
        loc=node.findtext("{*}loc")
        if loc:
            result.append({"url":canonical_url(loc),"modified":node.findtext("{*}lastmod")})
    return result


def parse_page(html: str, url: str) -> dict:
    soup=BeautifulSoup(html,"html.parser")
    for tag in soup(["script","style","nav","footer","header","noscript"]): tag.decompose()
    title=(soup.title.string.strip() if soup.title and soup.title.string else "")
    headings=[x.get_text(" ",strip=True) for x in soup.select("h1,h2,h3")]
    body="\n".join(x.get_text(" ",strip=True) for x in soup.select("main p,main li,article p,article li") if x.get_text(strip=True))
    if not body: body=soup.get_text(" ",strip=True)
    slug=urlparse(url).path.rstrip("/").split("/")[-1]
    text=f"{title}\n{' '.join(headings)}\n{body}"
    return {"canonical_url":canonical_url(url),"title":title or slug.replace("-"," ").title(),"slug":slug,
            "headings":headings,"summary":body[:500],"body_text":body,
            "content_digest":hashlib.sha256(text.encode()).hexdigest()}


def sync_inventory(conn, settings, *, client=None) -> dict:
    allowed={h.strip().lower() for h in settings.content_allowed_hosts.split(",") if h.strip()}
    own=client is None
    client=client or httpx.Client(timeout=settings.content_fetch_timeout_seconds,follow_redirects=True,
                                   headers={"User-Agent":"Newcraft-Mantle-Content/1.0"})
    run_id=conn.execute("insert into content_runs (run_type,status) values ('inventory_sync','running') returning id").fetchone()[0]
    try:
        response=client.get(settings.content_sitemap_url); response.raise_for_status()
        pages=[]
        for item in parse_sitemap(response.text):
            if urlparse(item["url"]).hostname not in allowed: raise InventoryError("Sitemap contains an unapproved host")
            page=client.get(item["url"]); page.raise_for_status()
            if urlparse(str(page.url)).hostname not in allowed: raise InventoryError("Page redirected outside approved hosts")
            if len(page.content)>settings.content_page_max_bytes: raise InventoryError("Content page exceeds size limit")
            if "html" not in page.headers.get("content-type","text/html"): raise InventoryError("Content page is not HTML")
            parsed=parse_page(page.text,item["url"]); parsed["modified_at"]=item["modified"]
            parsed["headings"] = Jsonb(parsed["headings"])
            pages.append(parsed)
        with conn.transaction():
            for page in pages:
                conn.execute("""insert into content_inventory
                  (canonical_url,title,slug,headings,summary,body_text,content_digest,modified_at,last_seen_at,removed_at)
                  values (%(canonical_url)s,%(title)s,%(slug)s,%(headings)s,%(summary)s,%(body_text)s,%(content_digest)s,%(modified_at)s,now(),null)
                  on conflict (canonical_url) do update set title=excluded.title,slug=excluded.slug,headings=excluded.headings,
                  summary=excluded.summary,body_text=excluded.body_text,content_digest=excluded.content_digest,
                  modified_at=excluded.modified_at,last_seen_at=now(),removed_at=null""",page)
            urls=[p["canonical_url"] for p in pages]
            if urls: conn.execute("update content_inventory set removed_at=coalesce(removed_at,now()) where not (canonical_url=any(%s))",(urls,))
            conn.execute("update content_runs set status='succeeded',finished_at=now(),usage=%s where id=%s",(Jsonb({"pages":len(pages)}),run_id))
        return {"pages":len(pages)}
    except Exception as exc:
        conn.execute("update content_runs set status='failed',finished_at=now(),safe_error=%s where id=%s",(str(exc)[:300],run_id))
        raise
    finally:
        if own: client.close()


def overlap_candidates(conn, query: str, limit: int = 5) -> list[dict]:
    tokens=set(re.findall(r"[a-z0-9]+",query.casefold()))
    found=[]
    for row in conn.execute("select id,canonical_url,title,slug,headings from content_inventory where removed_at is null").fetchall():
        hay=set(re.findall(r"[a-z0-9]+",f"{row[2]} {row[3]} {' '.join(row[4])}".casefold()))
        score=len(tokens&hay)/max(1,len(tokens|hay))
        if query.casefold() in f"{row[2]} {row[3]}".casefold(): score+=.5
        if score: found.append({"id":row[0],"url":row[1],"title":row[2],"score":round(score,3)})
    return sorted(found,key=lambda x:x["score"],reverse=True)[:limit]
