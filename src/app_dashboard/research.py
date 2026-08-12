"""Human research lists, notes, attachments, and their searchable index."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app_dashboard.discovery_watchlist import follow_app, watch_status

LIST_STATUSES = frozenset({"active", "archived"})
TARGET_COLUMNS = {
    "list": "research_list_id",
    "app": "discovered_app_id",
    "developer": "discovered_developer_id",
}


@dataclass(frozen=True)
class DetachedObject:
    digest: str
    object_key: str
    delete_physical: bool


def _now(value=None):
    return value or datetime.now(timezone.utc)


def _clean_required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field}-required")
    return value


def create_list(conn, title: str, description: str = "", *, now=None) -> dict:
    created_at = _now(now)
    row = conn.execute(
        """insert into research_lists
             (title,description,status,created_at,updated_at)
           values (%s,%s,'active',%s,%s)
           returning id,title,description,status,created_at,updated_at""",
        (_clean_required(title, "title"), description.strip(), created_at, created_at),
    ).fetchone()
    return _list_dict(row)


def update_list(
    conn, list_id: int, *, title: str, description: str, status: str, now=None,
) -> dict:
    if status not in LIST_STATUSES:
        raise ValueError("invalid-list-status")
    row = conn.execute(
        """update research_lists set title=%s,description=%s,status=%s,updated_at=%s
           where id=%s returning id,title,description,status,created_at,updated_at""",
        (_clean_required(title, "title"), description.strip(), status, _now(now), list_id),
    ).fetchone()
    if not row:
        raise LookupError("unknown-research-list")
    return _list_dict(row)


def _list_dict(row) -> dict:
    keys = ("id", "title", "description", "status", "created_at", "updated_at")
    return dict(zip(keys, row, strict=True))


def list_lists(conn, *, query: str | None = None, status: str | None = None) -> list[dict]:
    if status is not None and status not in LIST_STATUSES:
        raise ValueError("invalid-list-status")
    rows = conn.execute(
        """select list.id,list.title,list.description,list.status,
                  list.created_at,list.updated_at,
                  count(distinct member.discovered_app_id) app_count,
                  count(distinct developer_member.discovered_developer_id) developer_count,
                  count(distinct note.id) note_count,
                  count(distinct attachment.id) attachment_count
           from research_lists list
           left join research_list_apps member on member.research_list_id=list.id
           left join research_list_developers developer_member
             on developer_member.research_list_id=list.id
           left join research_notes note on note.research_list_id=list.id
           left join research_note_attachments attachment
             on attachment.research_note_id=note.id
           where (%s::text is null or list.status=%s)
             and (%s::text is null or list.title ilike '%%' || %s || '%%'
                  or list.description ilike '%%' || %s || '%%')
           group by list.id order by list.updated_at desc,list.id desc""",
        (status, status, query, query, query),
    ).fetchall()
    keys = (
        "id", "title", "description", "status", "created_at", "updated_at",
        "app_count", "developer_count", "note_count", "attachment_count",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


def get_list(conn, list_id: int) -> dict | None:
    row = conn.execute(
        """select id,title,description,status,created_at,updated_at
           from research_lists where id=%s""",
        (list_id,),
    ).fetchone()
    if not row:
        return None
    result = _list_dict(row)
    result["apps"] = [
        {"id": app_id, "handle": handle, "name": name, "added_at": added_at,
         "followed": followed}
        for app_id, handle, name, added_at, followed in conn.execute(
            """select app.id,app.handle,app.display_name,member.added_at,
                      coalesce(watch.active,false)
               from research_list_apps member
               join discovered_apps app on app.id=member.discovered_app_id
               left join discovery_watchlist watch on watch.discovered_app_id=app.id
               where member.research_list_id=%s
               order by coalesce(app.display_name,app.handle),app.handle""",
            (list_id,),
        ).fetchall()
    ]
    result["developers"] = [
        {"id": developer_id, "name": name, "shopify_url": shopify_url,
         "added_at": added_at, "last_scanned_at": last_scanned_at,
         "last_scan_error": last_scan_error, "app_count": app_count}
        for developer_id, name, shopify_url, added_at, last_scanned_at,
            last_scan_error, app_count in conn.execute(
            """select developer.id,developer.name,developer.shopify_url,
                      member.added_at,developer.last_scanned_at,
                      developer.last_scan_error,count(app_member.discovered_app_id)
               from research_list_developers member
               join discovered_developers developer
                 on developer.id=member.discovered_developer_id
               left join discovered_app_developers app_member
                 on app_member.discovered_developer_id=developer.id
               where member.research_list_id=%s
               group by developer.id,member.added_at
               order by developer.name,developer.id""",
            (list_id,),
        ).fetchall()
    ]
    result["notes"] = list_notes(conn, target_kind="list", target_id=list_id)
    result["app_count"] = len(result["apps"])
    result["developer_count"] = len(result["developers"])
    result["note_count"] = len(result["notes"])
    result["attachment_count"] = sum(
        note["attachment_count"] for note in result["notes"]
    )
    return result


def search_apps(
    conn, query: str, *, list_id: int | None = None, limit: int = 8,
) -> list[dict]:
    """Return a small, ranked catalog result set for the research app picker."""
    query = query.strip()
    if not query:
        return []
    limit = max(1, min(limit, 8))
    rows = conn.execute(
        """select app.handle,coalesce(nullif(app.display_name,''),app.handle),
                  coalesce(categories.names,''),
                  exists (
                    select 1 from research_list_apps member
                    where member.research_list_id=%s
                      and member.discovered_app_id=app.id
                  ) in_list
           from discovered_apps app
           left join lateral (
             select string_agg(category.name, ', ' order by category.name) names
             from discovered_app_categories category_member
             join discovery_categories category
               on category.id=category_member.category_id
             where category_member.discovered_app_id=app.id
           ) categories on true
           where app.handle ilike '%%' || %s || '%%'
              or coalesce(app.display_name,'') ilike '%%' || %s || '%%'
           order by
             case when lower(app.handle)=lower(%s)
                    or lower(coalesce(app.display_name,''))=lower(%s) then 0
                  when app.handle ilike %s || '%%'
                    or coalesce(app.display_name,'') ilike %s || '%%' then 1
                  else 2 end,
             coalesce(nullif(app.display_name,''),app.handle),app.handle
           limit %s""",
        (list_id, query, query, query, query, query, query, limit),
    ).fetchall()
    keys = ("handle", "name", "categories", "in_list")
    return [dict(zip(keys, row, strict=True)) for row in rows]


def search_developers(
    conn, query: str, *, list_id: int | None = None, limit: int = 8,
) -> list[dict]:
    query = query.strip()
    if not query:
        return []
    rows = conn.execute(
        """select developer.id,developer.name,developer.shopify_url,
                  count(app_member.discovered_app_id),
                  exists(select 1 from research_list_developers member
                         where member.research_list_id=%s
                           and member.discovered_developer_id=developer.id)
           from discovered_developers developer
           left join discovered_app_developers app_member
             on app_member.discovered_developer_id=developer.id
           where developer.name ilike '%%' || %s || '%%'
              or developer.shopify_url ilike '%%' || %s || '%%'
           group by developer.id
           order by case when lower(developer.name)=lower(%s) then 0
                         when developer.name ilike %s || '%%' then 1 else 2 end,
                    developer.name,developer.id limit %s""",
        (list_id, query, query, query, query, max(1, min(limit, 8))),
    ).fetchall()
    keys = ("id", "name", "shopify_url", "app_count", "in_list")
    return [dict(zip(keys, row, strict=True)) for row in rows]


def follow_developer_apps(conn, developer_id: int, *, now=None) -> int:
    handles = [row[0] for row in conn.execute(
        """select app.handle from discovered_app_developers member
           join discovered_apps app on app.id=member.discovered_app_id
           where member.discovered_developer_id=%s""", (developer_id,),
    ).fetchall()]
    for handle in handles:
        status = watch_status(conn, handle)
        if status is None or not status.active:
            follow_app(conn, handle, source="manual", now=_now(now))
    return len(handles)


def add_developer_to_list(conn, list_id: int, developer_id: int, *, now=None) -> bool:
    added_at = _now(now)
    if not conn.execute("select 1 from research_lists where id=%s", (list_id,)).fetchone():
        raise LookupError("unknown-research-list")
    if not conn.execute("select 1 from discovered_developers where id=%s", (developer_id,)).fetchone():
        raise LookupError("unknown-developer")
    inserted = conn.execute(
        """insert into research_list_developers
             (research_list_id,discovered_developer_id,added_at)
           values (%s,%s,%s) on conflict do nothing returning discovered_developer_id""",
        (list_id, developer_id, added_at),
    ).fetchone()
    conn.execute("update research_lists set updated_at=%s where id=%s", (added_at, list_id))
    follow_developer_apps(conn, developer_id, now=added_at)
    return inserted is not None


def remove_developer_from_list(conn, list_id: int, developer_id: int, *, now=None) -> bool:
    deleted = conn.execute(
        """delete from research_list_developers
           where research_list_id=%s and discovered_developer_id=%s
           returning discovered_developer_id""", (list_id, developer_id),
    ).fetchone()
    if deleted:
        conn.execute("update research_lists set updated_at=%s where id=%s", (_now(now), list_id))
    return deleted is not None


def add_app_to_list(conn, list_id: int, handle: str, *, now=None) -> bool:
    added_at = _now(now)
    app = conn.execute(
        "select id from discovered_apps where handle=%s", (handle,)
    ).fetchone()
    if not app:
        raise LookupError("unknown-discovered-app")
    if not conn.execute("select 1 from research_lists where id=%s", (list_id,)).fetchone():
        raise LookupError("unknown-research-list")
    inserted = conn.execute(
        """insert into research_list_apps
             (research_list_id,discovered_app_id,added_at)
           values (%s,%s,%s) on conflict do nothing returning discovered_app_id""",
        (list_id, app[0], added_at),
    ).fetchone()
    conn.execute(
        "update research_lists set updated_at=%s where id=%s", (added_at, list_id)
    )
    status = watch_status(conn, handle)
    if status is None or not status.active:
        follow_app(conn, handle, source="manual", now=added_at)
    return inserted is not None


def remove_app_from_list(conn, list_id: int, handle: str, *, now=None) -> bool:
    deleted = conn.execute(
        """delete from research_list_apps member using discovered_apps app
           where member.discovered_app_id=app.id and member.research_list_id=%s
             and app.handle=%s returning member.discovered_app_id""",
        (list_id, handle),
    ).fetchone()
    if deleted:
        conn.execute(
            "update research_lists set updated_at=%s where id=%s", (_now(now), list_id)
        )
    return deleted is not None


def create_note(
    conn, *, target_kind: str, target_id: int, title: str, body: str,
    author: str, now=None,
) -> dict:
    column = TARGET_COLUMNS.get(target_kind)
    if not column:
        raise ValueError("invalid-note-target")
    created_at = _now(now)
    row = conn.execute(
        f"""insert into research_notes
               (title,body,{column},author,created_at,updated_at)
             values (%s,%s,%s,%s,%s,%s)
             returning id,title,body,author,created_at,updated_at""",
        (_clean_required(title, "title"), body.strip(), target_id,
         _clean_required(author, "author"), created_at, created_at),
    ).fetchone()
    if target_kind == "list":
        conn.execute(
            "update research_lists set updated_at=%s where id=%s",
            (created_at, target_id),
        )
    return {
        "id": row[0], "title": row[1], "body": row[2], "author": row[3],
        "created_at": row[4], "updated_at": row[5], "target_kind": target_kind,
        "target_id": target_id, "attachments": [],
    }


def list_notes(conn, *, target_kind: str, target_id: int) -> list[dict]:
    column = TARGET_COLUMNS.get(target_kind)
    if not column:
        raise ValueError("invalid-note-target")
    rows = conn.execute(
        f"""select note.id,note.title,note.body,note.author,note.created_at,
                   note.updated_at,count(attachment.id)
            from research_notes note
            left join research_note_attachments attachment
              on attachment.research_note_id=note.id
            where note.{column}=%s group by note.id
            order by note.updated_at desc,note.id desc""",
        (target_id,),
    ).fetchall()
    keys = (
        "id", "title", "body", "author", "created_at", "updated_at",
        "attachment_count",
    )
    notes = [dict(zip(keys, row, strict=True)) for row in rows]
    for note in notes:
        note["attachments"] = note_attachments(conn, note["id"])
    return notes


def get_note(conn, note_id: int) -> dict | None:
    row = conn.execute(
        """select id,title,body,author,created_at,updated_at,research_list_id,
                  discovered_app_id,discovered_developer_id
           from research_notes where id=%s""",
        (note_id,),
    ).fetchone()
    if not row:
        return None
    target_values = row[6:]
    index = next(index for index, value in enumerate(target_values) if value is not None)
    result = {
        "id": row[0], "title": row[1], "body": row[2], "author": row[3],
        "created_at": row[4], "updated_at": row[5],
        "target_kind": tuple(TARGET_COLUMNS)[index], "target_id": target_values[index],
    }
    result["attachments"] = note_attachments(conn, note_id)
    return result


def update_note(
    conn, note_id: int, *, title: str, body: str, now=None,
) -> dict:
    updated_at = _now(now)
    row = conn.execute(
        """update research_notes
           set title=%s,body=%s,updated_at=%s
           where id=%s
           returning id,title,body,author,created_at,updated_at""",
        (_clean_required(title, "title"), body.strip(), updated_at, note_id),
    ).fetchone()
    if not row:
        raise LookupError("unknown-research-note")
    result = get_note(conn, note_id)
    if result["target_kind"] == "list":
        conn.execute(
            "update research_lists set updated_at=%s where id=%s",
            (updated_at, result["target_id"]),
        )
    return result


def delete_note(conn, note_id: int) -> list[DetachedObject]:
    objects = conn.execute(
        """select distinct object.digest,object.object_key
           from research_note_attachments attachment
           join research_attachment_objects object on object.digest=attachment.digest
           where attachment.research_note_id=%s""",
        (note_id,),
    ).fetchall()
    deleted = conn.execute(
        "delete from research_notes where id=%s returning id", (note_id,)
    ).fetchone()
    if not deleted:
        raise LookupError("unknown-research-note")
    detached = []
    for digest, object_key in objects:
        referenced = conn.execute(
            "select 1 from research_note_attachments where digest=%s limit 1",
            (digest,),
        ).fetchone()
        if not referenced:
            conn.execute(
                "delete from research_attachment_objects where digest=%s", (digest,)
            )
        detached.append(DetachedObject(digest, object_key, not bool(referenced)))
    return detached


def attach_object(
    conn, note_id: int, *, digest: str, object_key: str, mime_type: str,
    byte_size: int, original_filename: str, now=None,
) -> dict:
    if not conn.execute("select 1 from research_notes where id=%s", (note_id,)).fetchone():
        raise LookupError("unknown-research-note")
    created_at = _now(now)
    conn.execute(
        """insert into research_attachment_objects
             (digest,object_key,mime_type,byte_size,created_at)
           values (%s,%s,%s,%s,%s) on conflict (digest) do nothing""",
        (digest, object_key, mime_type, byte_size, created_at),
    )
    position = conn.execute(
        """select coalesce(max(position),-1)+1 from research_note_attachments
           where research_note_id=%s""",
        (note_id,),
    ).fetchone()[0]
    row = conn.execute(
        """insert into research_note_attachments
             (research_note_id,digest,original_filename,position,created_at)
           values (%s,%s,%s,%s,%s)
           on conflict (research_note_id,digest) do update set
             original_filename=excluded.original_filename
           returning id,position""",
        (note_id, digest, _clean_required(original_filename, "filename"),
         position, created_at),
    ).fetchone()
    return {"id": row[0], "digest": digest, "position": row[1]}


def note_attachments(conn, note_id: int) -> list[dict]:
    rows = conn.execute(
        """select attachment.id,attachment.original_filename,attachment.position,
                  object.digest,object.object_key,object.mime_type,object.byte_size,
                  attachment.created_at
           from research_note_attachments attachment
           join research_attachment_objects object on object.digest=attachment.digest
           where attachment.research_note_id=%s
           order by attachment.position,attachment.id""",
        (note_id,),
    ).fetchall()
    keys = (
        "id", "filename", "position", "digest", "object_key", "mime_type",
        "byte_size", "created_at",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


def attachment_detail(conn, attachment_id: int) -> dict | None:
    row = conn.execute(
        """select attachment.id,attachment.research_note_id,
                  attachment.original_filename,object.digest,object.object_key,
                  object.mime_type,object.byte_size
           from research_note_attachments attachment
           join research_attachment_objects object on object.digest=attachment.digest
           where attachment.id=%s""",
        (attachment_id,),
    ).fetchone()
    keys = ("id", "note_id", "filename", "digest", "object_key", "mime_type", "byte_size")
    return dict(zip(keys, row, strict=True)) if row else None


def research_index(
    conn, *, query: str | None = None, item_type: str | None = None,
    list_id: int | None = None, status: str | None = None,
    start=None, end=None, limit: int = 250,
) -> list[dict]:
    if item_type not in {None, "list", "note", "attachment"}:
        raise ValueError("invalid-research-type")
    rows = conn.execute(
        """with items as (
             select 'list' item_type,list.id item_id,list.title,
                    list.description summary,'list' context_kind,list.id context_id,
                    list.title context_title,null::text context_handle,
                    list.id list_id,list.title list_title,list.status list_status,
                    null::text filename,null::text mime_type,'system' author,
                    list.updated_at
             from research_lists list
             union all
             select 'note',note.id,note.title,note.body,
                    case when note.research_list_id is not null then 'list'
                         when note.discovered_app_id is not null then 'app'
                         else 'developer' end,
                    coalesce(note.research_list_id,note.discovered_app_id,
                             note.discovered_developer_id),
                    coalesce(list.title,app.display_name,app.handle,developer.name),
                    app.handle,note.research_list_id,list.title,list.status,
                    null,null,note.author,note.updated_at
             from research_notes note
             left join research_lists list on list.id=note.research_list_id
             left join discovered_apps app on app.id=note.discovered_app_id
             left join discovered_developers developer
               on developer.id=note.discovered_developer_id
             union all
             select 'attachment',attachment.id,attachment.original_filename,'',
                    case when note.research_list_id is not null then 'list'
                         when note.discovered_app_id is not null then 'app'
                         else 'developer' end,
                    coalesce(note.research_list_id,note.discovered_app_id,
                             note.discovered_developer_id),
                    coalesce(list.title,app.display_name,app.handle,developer.name),
                    app.handle,note.research_list_id,list.title,list.status,
                    attachment.original_filename,object.mime_type,note.author,
                    attachment.created_at
             from research_note_attachments attachment
             join research_attachment_objects object on object.digest=attachment.digest
             join research_notes note on note.id=attachment.research_note_id
             left join research_lists list on list.id=note.research_list_id
             left join discovered_apps app on app.id=note.discovered_app_id
             left join discovered_developers developer
               on developer.id=note.discovered_developer_id
           )
           select item_type,item_id,title,summary,context_kind,context_id,
                  context_title,context_handle,list_id,list_title,list_status,
                  filename,mime_type,author,updated_at
           from items
           where (%s::text is null or item_type=%s)
             and (%s::bigint is null or list_id=%s or (item_type='list' and item_id=%s))
             and (%s::date is null or updated_at::date >= %s)
             and (%s::date is null or updated_at::date <= %s)
             and (%s::text is null or list_status=%s)
             and (%s::text is null or title ilike '%%' || %s || '%%'
                  or summary ilike '%%' || %s || '%%'
                  or context_title ilike '%%' || %s || '%%'
                  or filename ilike '%%' || %s || '%%')
           order by updated_at desc,item_type,item_id desc limit %s""",
        (item_type, item_type, list_id, list_id, list_id,
         start, start, end, end, status, status,
         query, query, query, query, query, limit),
    ).fetchall()
    keys = (
        "type", "id", "title", "summary", "context_kind", "context_id",
        "context_title", "context_handle", "list_id", "list_title", "list_status",
        "filename", "mime_type", "author", "updated_at",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


def target_research(conn, *, target_kind: str, target_id: int) -> dict:
    notes = list_notes(conn, target_kind=target_kind, target_id=target_id)
    lists = []
    if target_kind == "app":
        lists = [
            {"id": row[0], "title": row[1], "status": row[2], "added_at": row[3]}
            for row in conn.execute(
                """select list.id,list.title,list.status,member.added_at
                   from research_list_apps member
                   join research_lists list on list.id=member.research_list_id
                   where member.discovered_app_id=%s
                   order by list.status,list.title""",
                (target_id,),
            ).fetchall()
        ]
    return {"notes": notes, "lists": lists}
