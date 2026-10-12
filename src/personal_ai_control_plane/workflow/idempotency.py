"""Idempotency keys: same trigger => same execution, never a restart.

Key = SHA256(workflow_name + issue_id + trigger_hash) where trigger_hash is a
canonical-JSON SHA256 of the whole trigger context. Keys live in the
workflow_idempotency SQLite table (see db.py SCHEMA).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..db import connect, init_db, utcnow


def _trigger_hash(trigger_context: dict[str, Any]) -> str:
    canonical = json.dumps(
        trigger_context, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_idempotency_key(workflow_name: str, trigger_context: dict[str, Any]) -> str:
    """Deterministic key for (workflow, trigger). Same input => same key."""
    issue_id = str((trigger_context or {}).get("issue_id") or "")
    material = f"{workflow_name}|{issue_id}|{_trigger_hash(trigger_context or {})}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def check_and_register(key: str, execution_id: str) -> str | None:
    """Register key -> execution_id atomically.

    Returns None when the key is new (now registered to execution_id).
    Returns the existing execution_id when this key was seen before
    (duplicate trigger; caller must reuse, not restart).
    """
    init_db()
    try:
        with connect() as conn:
            existing = conn.execute(
                "SELECT execution_id FROM workflow_idempotency WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                return str(existing["execution_id"])
            conn.execute(
                "INSERT INTO workflow_idempotency (idempotency_key, execution_id, created_at)"
                " VALUES (?, ?, ?)",
                (key, execution_id, utcnow()),
            )
    except sqlite3.IntegrityError:
        # Concurrent insert raced us: someone else registered first.
        with connect() as conn:
            existing = conn.execute(
                "SELECT execution_id FROM workflow_idempotency WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        return str(existing["execution_id"]) if existing is not None else execution_id
    return None
