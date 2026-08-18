from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .settings import database_path

# v0.3.1 deliberately owns only logical bindings. Multica owns work resources;
# Open WebUI owns knowledge. Legacy v0.3 tables may still exist in an upgraded
# database, but this version does not read/write them.
SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS contexts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS multica_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id TEXT NOT NULL REFERENCES contexts(id) ON DELETE CASCADE,
    workspace_ref TEXT NOT NULL,
    project_ref TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'related',
    profile TEXT,
    UNIQUE(context_id, workspace_ref, project_ref)
);

CREATE TABLE IF NOT EXISTS openwebui_knowledge_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id TEXT NOT NULL REFERENCES contexts(id) ON DELETE CASCADE,
    knowledge_id TEXT NOT NULL,
    label TEXT,
    role TEXT NOT NULL DEFAULT 'related',
    UNIQUE(context_id, knowledge_id)
);

CREATE INDEX IF NOT EXISTS idx_bindings_context ON multica_bindings(context_id);
CREATE INDEX IF NOT EXISTS idx_owui_knowledge_context ON openwebui_knowledge_bindings(context_id);

CREATE TABLE IF NOT EXISTS loop_runs (
    run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    project_path TEXT NOT NULL,
    profile_name TEXT,
    issue_ref TEXT,
    dry_run INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'running',
    evidence_uri TEXT,
    diff_summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    execution_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_loop_runs_created ON loop_runs(created_at);
-- idx_loop_runs_execution_id is created in _migrate_legacy() so legacy
-- databases get their ALTER TABLE before any index references the column.

CREATE TABLE IF NOT EXISTS execution_records (
    execution_id TEXT PRIMARY KEY,
    project_path TEXT NOT NULL,
    profile_name TEXT,
    target TEXT,
    issue_ref TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_records_status ON execution_records(status);
"""


def _migrate_legacy(conn: sqlite3.Connection) -> None:
    """Bring pre-execution_id databases up to date (idempotent)."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(loop_runs)")}
    if "execution_id" not in columns:
        conn.execute("ALTER TABLE loop_runs ADD COLUMN execution_id TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_loop_runs_execution_id ON loop_runs(execution_id)"
    )


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> Path:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate_legacy(conn)
    return database_path()


def aliases_dump(values: list[str]) -> str:
    return json.dumps(
        [x.strip() for x in values if x and x.strip()], ensure_ascii=False
    )


def aliases_load(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
        return [str(x) for x in data] if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []
