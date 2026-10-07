"""User-scoped local search and primary-record retrieval, independent of AI."""
from core.db import conn, db_lock
from core.search_query import predicate, register, tokens

SPECS = {
    "note": ("notes", "note_id", ["title", "text"], "deleted_at IS NULL"),
    "task": ("tasks", "task_id", ["title", "description"], "1"),
    "memory": ("user_memories", "memory_id", ["value_json", "evidence", "memory_key"], "status='active'"),
    "company": ("sales_companies", "company_id", ["name", "industry", "website", "notes"], "deleted_at IS NULL AND status='active'"),
    "contact": ("sales_contacts", "contact_id", ["full_name", "position", "email", "phone", "telegram", "notes"], "deleted_at IS NULL AND status='active'"),
    "interaction": ("sales_interactions", "interaction_id", ["summary", "outcome", "next_step"], "1"),
    "commitment": ("sales_commitments", "commitment_id", ["title"], "1"),
}


def memory_entity(user_id, kind, item_id):
    if kind == "reminder":
        from core.reminder_store import SELECT_COLUMNS
        with db_lock:
            cursor=conn.execute(f"SELECT {SELECT_COLUMNS} FROM reminders WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL", (user_id,item_id))
            row=cursor.fetchone()
            return dict(zip([c[0] for c in cursor.description],row)) if row else None
    if kind not in SPECS:
        return None
    table, identifier, _, condition = SPECS[kind]
    with db_lock:
        cursor = conn.execute(f"SELECT * FROM {table} WHERE user_id=? AND {identifier}=? AND {condition}", (user_id, item_id))
        row = cursor.fetchone()
        if row is None:
            return None
        item = dict(zip([c[0] for c in cursor.description], row))
        if kind == "commitment":
            item["actions"] = [{"kind": k, "target_id": target} for k, target in conn.execute("SELECT kind,target_id FROM commitment_actions WHERE user_id=? AND commitment_id=?", (user_id, item_id))]
        if kind in {"commitment", "interaction", "contact"}:
            for other_kind in ("company", "contact"):
                other_id = item.get(other_kind+"_id")
                if other_id and kind != other_kind:
                    other = memory_entity(user_id, other_kind, other_id)
                    item[other_kind+"_name"] = (other or {}).get("name") or (other or {}).get("full_name")
    return item


def result_item(kind, item):
    identifier = SPECS[kind][1]
    title = item.get("title") or item.get("name") or item.get("full_name") or item.get("summary") or item.get("memory_key") or "Без названия"
    snippet = item.get("text") or item.get("description") or item.get("value_json") or item.get("notes") or item.get("outcome") or ""
    return {"kind": kind, "id": item[identifier], "title": str(title)[:300], "snippet": str(snippet)[:240],
            "date": item.get("updated_at") or item.get("created_at"), "source_type": item.get("source_type"), "source_id": item.get("source_id")}


def search_local(user_id, query, *, kind=None, before=None, limit=20):
    words = tokens(query)
    kinds = [kind] if kind in SPECS else list(SPECS)
    limit = max(1, min(int(limit), 50))
    groups = {}
    with db_lock:
        register(conn)
        for name in kinds:
            table, identifier, fields, condition = SPECS[name]
            match, parameters = predicate(fields, words)
            cursor = conn.execute(f"SELECT * FROM {table} WHERE user_id=? AND {condition} AND ({match}) AND {identifier}<? ORDER BY {identifier} DESC LIMIT ?", (user_id, *parameters, before or 9223372036854775807, limit+1))
            columns = [c[0] for c in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            groups[name] = {"items": [result_item(name, row) for row in rows[:limit]], "next_cursor": rows[limit-1][identifier] if len(rows)>limit else None}
    return groups
