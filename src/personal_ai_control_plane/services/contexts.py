from __future__ import annotations

from typing import Any

from ..db import aliases_dump, aliases_load, connect, init_db, utcnow
from ..errors import ContextNotFound
from ..models import (
    ContextCreate,
    ContextUpdate,
    MulticaBindingInput,
    OpenWebUIKnowledgeBindingInput,
)


def _context_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "context_id": row["id"],
        "name": row["name"],
        "aliases": aliases_load(row["aliases_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _children(
    conn, context_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bindings = [
        {
            "id": r["id"],
            "workspace": r["workspace_ref"],
            "project": r["project_ref"],
            "role": r["role"],
            "profile": r["profile"],
        }
        for r in conn.execute(
            "SELECT * FROM multica_bindings WHERE context_id=? "
            "ORDER BY CASE role WHEN 'primary' THEN 0 ELSE 1 END, id",
            (context_id,),
        )
    ]
    knowledge = [
        {
            "id": r["id"],
            "knowledge_id": r["knowledge_id"],
            "label": r["label"],
            "role": r["role"],
        }
        for r in conn.execute(
            "SELECT * FROM openwebui_knowledge_bindings WHERE context_id=? "
            "ORDER BY CASE role WHEN 'primary' THEN 0 ELSE 1 END, id",
            (context_id,),
        )
    ]
    return bindings, knowledge


def list_contexts() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        result = []
        for row in conn.execute("SELECT * FROM contexts ORDER BY name COLLATE NOCASE"):
            item = _context_row(row)
            bindings, knowledge = _children(conn, row["id"])
            item.update(
                {
                    "multica_bindings": bindings,
                    "openwebui_knowledge_bindings": knowledge,
                }
            )
            result.append(item)
        return result


def get_context(context_id: str) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM contexts WHERE id=?", (context_id,)
        ).fetchone()
        if not row:
            raise ContextNotFound(context_id)
        item = _context_row(row)
        bindings, knowledge = _children(conn, context_id)
        item.update(
            {"multica_bindings": bindings, "openwebui_knowledge_bindings": knowledge}
        )
        return item


def configured_multica_bindings(
    context_id: str | None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    contexts = [get_context(context_id)] if context_id else list_contexts()
    return [
        (context, binding)
        for context in contexts
        for binding in context.get("multica_bindings", [])
    ]


def create_context(payload: ContextCreate) -> dict[str, Any]:
    init_db()
    now = utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO contexts(id,name,aliases_json,created_at,updated_at) VALUES(?,?,?,?,?)",
            (payload.id, payload.name, aliases_dump(payload.aliases), now, now),
        )
        for binding in payload.multica_bindings:
            _insert_binding(conn, payload.id, binding)
        for binding in payload.openwebui_knowledge_bindings:
            _insert_openwebui_knowledge(conn, payload.id, binding)
    return get_context(payload.id)


def update_context(context_id: str, payload: ContextUpdate) -> dict[str, Any]:
    get_context(context_id)
    updates, params = [], []
    if payload.name is not None:
        updates.append("name=?")
        params.append(payload.name)
    if payload.aliases is not None:
        updates.append("aliases_json=?")
        params.append(aliases_dump(payload.aliases))
    if updates:
        updates.append("updated_at=?")
        params.append(utcnow())
        params.append(context_id)
        with connect() as conn:
            conn.execute(f"UPDATE contexts SET {', '.join(updates)} WHERE id=?", params)
    return get_context(context_id)


def delete_context(context_id: str) -> dict[str, Any]:
    get_context(context_id)
    with connect() as conn:
        conn.execute("DELETE FROM contexts WHERE id=?", (context_id,))
    return {"deleted": True, "context_id": context_id}


def _insert_binding(conn, context_id: str, payload: MulticaBindingInput) -> int:
    if payload.role == "primary":
        conn.execute(
            "UPDATE multica_bindings SET role='related' WHERE context_id=? AND role='primary'",
            (context_id,),
        )
    cur = conn.execute(
        "INSERT INTO multica_bindings(context_id,workspace_ref,project_ref,role,profile) VALUES(?,?,?,?,?)",
        (context_id, payload.workspace, payload.project, payload.role, payload.profile),
    )
    return int(cur.lastrowid)


def add_multica_binding(
    context_id: str, payload: MulticaBindingInput
) -> dict[str, Any]:
    get_context(context_id)
    with connect() as conn:
        binding_id = _insert_binding(conn, context_id, payload)
    return {"binding_id": binding_id, **get_context(context_id)}


def delete_multica_binding(context_id: str, binding_id: int) -> dict[str, Any]:
    get_context(context_id)
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM multica_bindings WHERE context_id=? AND id=?",
            (context_id, binding_id),
        )
        if cur.rowcount == 0:
            raise ContextNotFound(f"Multica binding {binding_id} in {context_id}")
    return get_context(context_id)


def _insert_openwebui_knowledge(
    conn, context_id: str, payload: OpenWebUIKnowledgeBindingInput
) -> int:
    if payload.role == "primary":
        conn.execute(
            "UPDATE openwebui_knowledge_bindings SET role='related' WHERE context_id=? AND role='primary'",
            (context_id,),
        )
    cur = conn.execute(
        "INSERT INTO openwebui_knowledge_bindings(context_id,knowledge_id,label,role) VALUES(?,?,?,?)",
        (context_id, payload.knowledge_id, payload.label, payload.role),
    )
    return int(cur.lastrowid)


def add_openwebui_knowledge_binding(
    context_id: str, payload: OpenWebUIKnowledgeBindingInput
) -> dict[str, Any]:
    get_context(context_id)
    with connect() as conn:
        binding_id = _insert_openwebui_knowledge(conn, context_id, payload)
    return {"knowledge_binding_id": binding_id, **get_context(context_id)}


def delete_openwebui_knowledge_binding(
    context_id: str, binding_id: int
) -> dict[str, Any]:
    get_context(context_id)
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM openwebui_knowledge_bindings WHERE context_id=? AND id=?",
            (context_id, binding_id),
        )
        if cur.rowcount == 0:
            raise ContextNotFound(
                f"Open WebUI knowledge binding {binding_id} in {context_id}"
            )
    return get_context(context_id)


def _score(text: str, ctx: dict[str, Any]) -> int:
    t = text.casefold()
    candidates = [ctx["id"], ctx["name"], *ctx.get("aliases", [])]
    score = 0
    for raw in candidates:
        c = str(raw).strip().casefold()
        if not c:
            continue
        if t == c:
            score = max(score, 100)
        elif c in t:
            score = max(score, 80 + min(len(c), 19))
        else:
            tokens = [x for x in c.replace("-", " ").replace("_", " ").split() if x]
            if tokens and all(x in t for x in tokens):
                score = max(score, 60 + len(tokens))
    return score


def resolve_context(text: str) -> dict[str, Any]:
    contexts = list_contexts()
    ranked = sorted(
        ((ctx, _score(text, ctx)) for ctx in contexts), key=lambda x: x[1], reverse=True
    )
    if not ranked or ranked[0][1] <= 0:
        raise ContextNotFound(f"No configured context matched: {text}")
    ctx, score = ranked[0]
    return {"match_score": score, **ctx}
