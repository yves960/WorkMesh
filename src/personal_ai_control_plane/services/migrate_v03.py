from __future__ import annotations

from ..db import connect, init_db


def migrate_v03_legacy_tables() -> dict[str, int | str]:
    """Migrate only references that still belong in v0.3.1.

    v0.3 local_roots and local knowledge are intentionally ignored because
    Multica Project Resources and Open WebUI Knowledge are the systems of record.
    Existing contexts + multica_bindings already use the same tables.
    """
    init_db()
    migrated = 0
    ignored_local_roots = 0
    ignored_local_knowledge = 0
    with connect() as conn:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "local_roots" in tables:
            ignored_local_roots = conn.execute(
                "SELECT COUNT(*) FROM local_roots"
            ).fetchone()[0]
        if "knowledge_sources" in tables:
            rows = list(conn.execute("SELECT * FROM knowledge_sources"))
            for r in rows:
                if r["source_type"] == "openwebui_collection":
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO openwebui_knowledge_bindings(context_id,knowledge_id,label,role) VALUES(?,?,?,?)",
                        (r["context_id"], r["location"], r["purpose"], "related"),
                    )
                    migrated += max(cur.rowcount, 0)
                else:
                    ignored_local_knowledge += 1
    return {
        "migrated_openwebui_knowledge_bindings": migrated,
        "ignored_legacy_local_roots": ignored_local_roots,
        "ignored_legacy_local_knowledge_sources": ignored_local_knowledge,
        "note": "Ignored entries are not deleted; v0.3.1 simply stops treating them as owned Control Plane assets.",
    }
