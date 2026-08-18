"""P1: execution_id full lifecycle (auto/explicit -> SQL -> callback -> 404).

Covers: auto-generation format, explicit adoption, callback correlation via
execution_id, unknown-execution_id 404, execution_records table + loop_runs
column/index, and the deprecated run_id compatibility mode.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from personal_ai_control_plane import loops
from personal_ai_control_plane.api import app

EXEC_ID_RE = re.compile(r"^exec-\d{8}-[0-9a-f]{6}$")


class FakePopen:
    last_cmd: list[str] | None = None

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.pid = 4242
        FakePopen.last_cmd = list(cmd)

    def wait(self, timeout=None):
        return 0


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    monkeypatch.setattr(loops.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(loops.shutil, "which", lambda _n: "/usr/local/bin/loop")
    monkeypatch.setenv("AICP_LOOP_EXECUTABLE", "loop")
    loops.reset_loops_for_tests()
    return TestClient(app)


def _db(tmp_path) -> sqlite3.Connection:
    return sqlite3.connect(Path(tmp_path) / "data" / "control-plane.db")


def _run_payload(**overrides) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": "ws-1",
        "project_path": "~/projects/self/WorkMesh",
        "profile_name": "sast-fixer",
        "target": "com.demo.SastServiceTest",
        "issue_ref": "MUL-42",
        "dry_run": True,
    }
    payload.update(overrides)
    return payload


def test_auto_generated_execution_id_format(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    body = c.post("/api/v1/loop/run", json=_run_payload()).json()

    assert EXEC_ID_RE.match(body["execution_id"]), body["execution_id"]
    argv = FakePopen.last_cmd or []
    assert argv[argv.index("--execution-id") + 1] == body["execution_id"]


def test_explicit_execution_id_is_adopted(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    body = c.post(
        "/api/v1/loop/run", json=_run_payload(execution_id="exec-20260818-cafe01")
    ).json()

    assert body["execution_id"] == "exec-20260818-cafe01"
    db = _db(tmp_path)
    row = db.execute(
        "SELECT execution_id, project_path, profile_name, target, issue_ref, status"
        " FROM execution_records"
    ).fetchone()
    db.close()
    assert row == (
        "exec-20260818-cafe01",
        "~/projects/self/WorkMesh",
        "sast-fixer",
        "com.demo.SastServiceTest",
        "MUL-42",
        "running",
    )


def test_callback_correlates_via_execution_id(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    run = c.post(
        "/api/v1/loop/run", json=_run_payload(execution_id="exec-20260818-dead02")
    ).json()
    loops.reset_loops_for_tests()  # simulate restart: pure execution_id path

    r = c.post(
        "/api/v1/loop/callback",
        json={
            "execution_id": "exec-20260818-dead02",
            "status": "pass",
            "evidence_uri": "file:///tmp/e.md",
            "diff_summary": "+1 -1",
        },
    )

    assert r.status_code == 200
    assert r.json()["execution_id"] == "exec-20260818-dead02"
    assert r.json()["status"] == "pass"
    db = _db(tmp_path)
    run_row = db.execute(
        "SELECT status, execution_id FROM loop_runs WHERE run_id = ?",
        (run["run_id"],),
    ).fetchone()
    exec_row = db.execute(
        "SELECT status FROM execution_records WHERE execution_id = ?",
        ("exec-20260818-dead02",),
    ).fetchone()
    db.close()
    assert run_row == ("pass", "exec-20260818-dead02")
    assert exec_row == ("pass",)


def test_callback_unknown_execution_id_returns_404(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    r = c.post(
        "/api/v1/loop/callback",
        json={"execution_id": "exec-19700101-nope00", "status": "pass"},
    )

    assert r.status_code == 404
    assert "exec-19700101-nope00" in r.json()["detail"]


def test_callback_without_any_identifier_returns_422(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    r = c.post("/api/v1/loop/callback", json={"status": "pass"})

    assert r.status_code == 422


def test_execution_records_table_and_loop_runs_column_and_index(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    c.post("/api/v1/loop/run", json=_run_payload())  # forces init_db + insert

    db = _db(tmp_path)
    exec_cols = {row[1] for row in db.execute("PRAGMA table_info(execution_records)")}
    loop_cols = {row[1] for row in db.execute("PRAGMA table_info(loop_runs)")}
    indexes = {row[1] for row in db.execute("PRAGMA index_list('loop_runs')")}
    db.close()
    assert {
        "execution_id",
        "project_path",
        "profile_name",
        "target",
        "issue_ref",
        "status",
        "created_at",
        "updated_at",
    } <= exec_cols
    assert "execution_id" in loop_cols
    assert "idx_loop_runs_execution_id" in indexes


def test_legacy_run_id_callback_mode_still_works(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    run = c.post("/api/v1/loop/run", json=_run_payload()).json()

    r = c.post(
        "/api/v1/loop/callback",
        json={"run_id": run["run_id"], "status": "fail", "diff_summary": "boom"},
    )

    assert r.status_code == 200
    assert r.json()["status"] == "fail"
    assert r.json()["execution_id"] == run["execution_id"]


def test_legacy_db_without_execution_id_column_is_migrated(monkeypatch, tmp_path):
    # Old database created before the execution_id era must be auto-upgraded.
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    db_path = Path(tmp_path) / "data" / "control-plane.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE loop_runs (
            run_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            project_path TEXT NOT NULL,
            profile_name TEXT,
            issue_ref TEXT NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'running',
            evidence_uri TEXT,
            diff_summary TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );
        """
    )
    legacy.commit()
    legacy.close()

    c = _client(monkeypatch, tmp_path)
    body = c.post("/api/v1/loop/run", json=_run_payload()).json()

    assert EXEC_ID_RE.match(body["execution_id"])
    db = sqlite3.connect(db_path)
    cols = {row[1] for row in db.execute("PRAGMA table_info(loop_runs)")}
    idx = {row[1] for row in db.execute("PRAGMA index_list('loop_runs')")}
    db.close()
    assert "execution_id" in cols
    assert "idx_loop_runs_execution_id" in idx
