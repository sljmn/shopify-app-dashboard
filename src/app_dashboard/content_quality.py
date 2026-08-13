"""Deterministic publication checks for accepted content."""

from __future__ import annotations

import re
from urllib.parse import urlparse


def check_content(project: dict, profile: dict, text: str, *, internal_links: list[str] | None = None) -> list[dict]:
    internal_links=internal_links or re.findall(r'href=["\'](https://newcraft\.dev/[^"\']+)',text)
    checks=[]
    def add(name, ok, evidence, *, warning=False):
        checks.append({"name":name,"severity":"pass" if ok else ("warning" if warning else "block"),"evidence":evidence})
    add("Accepted draft",bool(text.strip()),"A non-empty draft is required")
    add("Overlap resolved",project["overlap_status"] in {"clear","differentiate","update_existing"},f"Current status: {project['overlap_status']}")
    if project["channel"] == "seo_article":
        pillar=profile.get("pillar_url")
        add("Pillar link",not pillar or pillar in text,"Include the configured app pillar URL")
        add("Internal links",2 <= len(set(internal_links)) <= 4,f"Found {len(set(internal_links))}; expected 2 to 4",warning=True)
    add("No unresolved placeholders",not re.search(r"\b(TODO|TBD|INSERT|PLACEHOLDER)\b|\[[A-Z _]{3,}\]",text,re.I),"Remove unfinished placeholders")
    add("No emoji or em dash","—" not in text and not re.search(r"[\U0001F300-\U0001FAFF]",text),"Use direct prose without emoji or em dashes")
    paragraphs=[re.sub(r"\s+"," ",p.strip()).casefold() for p in re.split(r"\n\s*\n",text) if len(p.strip())>30]
    add("No duplicate paragraphs",len(paragraphs)==len(set(paragraphs)),"Repeated paragraphs reduce usefulness")
    return checks


def run_checks(conn, project: dict, profile: dict, text: str, version_id: int | None) -> list[dict]:
    findings=check_content(project,profile,text)
    conn.execute("delete from content_quality_checks where project_id=%s",(project["id"],))
    for finding in findings:
        conn.execute("insert into content_quality_checks (project_id,version_id,check_name,severity,evidence) values (%s,%s,%s,%s,%s)",
                     (project["id"],version_id,finding["name"],finding["severity"],finding["evidence"]))
    return findings


def publication_ready(project: dict, checks: list[dict]) -> tuple[bool,str]:
    if not checks: return False,"Run quality checks first"
    blockers=[x["name"] for x in checks if x["severity"]=="block"]
    return (not blockers, "Ready" if not blockers else "Blocked by: "+", ".join(blockers))
