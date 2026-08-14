"""Integration tests for the internal loop endpoints (TestClient)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from personal_ai_control_plane import loops
from personal_ai_control_plane.api import app


class FakePopen:
    """Stands in for subprocess.Popen so no real process is spawned."""

    last_cmd: list[str] | None = None

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.pid = 4242
        FakePopen.last_cmd = cmd

    def wait(self, timeout=None):
        return 0


def _client(monkeypatch, tmp_path, executable="loop", *, fake_which=True) -> TestClient:
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    monkeypatch.setattr(loops.subprocess, "Popen", FakePopen)
    if fake_which:
        monkeypatch.setattr(loops.shutil, "which", lambda _name: "/usr/local/bin/loop")
    monkeypatch.setenv("AICP_LOOP_EXECUTABLE", executable)
    loops.reset_loops_for_tests()
    return TestClient(app)


def _run_payload(**overrides) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": "ws-1",
        "project_path": "~/projects/self/WorkMesh",
        "profile_name": "forger",
        "issue_ref": "MUL-42",
        "dry_run": True,
    }
    payload.update(overrides)
    return payload


def test_loop_run_returns_run_id_without_blocking(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    r = c.post("/api/v1/loop/run", json=_run_payload())

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "running"
    assert body["run_id"]
    assert body["dry_run"] is True
    assert FakePopen.last_cmd is not None
    assert FakePopen.last_cmd[0] == "loop"
    assert FakePopen.last_cmd[1] == "run"
    assert "--dry-run" in FakePopen.last_cmd
    assert "--workspace" in FakePopen.last_cmd


def test_loop_run_without_dry_run_omits_flag(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    r = c.post("/api/v1/loop/run", json=_run_payload(dry_run=False))

    assert r.status_code == 200
    assert "--dry-run" not in (FakePopen.last_cmd or [])


def test_loop_status_lists_active_run(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    run = c.post("/api/v1/loop/run", json=_run_payload()).json()

    r = c.get("/loop/status")

    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["active_loops"][0]["run_id"] == run["run_id"]
    assert body["active_loops"][0]["issue_ref"] == "MUL-42"


def test_loop_callback_updates_memory_and_sqlite(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    run = c.post("/api/v1/loop/run", json=_run_payload()).json()

    r = c.post(
        "/api/v1/loop/callback",
        json={
            "run_id": run["run_id"],
            "status": "pass",
            "evidence_uri": "file:///tmp/evidence.md",
            "diff_summary": "+42 -7",
            "profile_name": "forger",
        },
    )

    assert r.status_code == 200
    assert r.json()["status"] == "pass"

    snapshot = c.get("/loop/status").json()["active_loops"][0]
    assert snapshot["status"] == "pass"
    assert snapshot["evidence_uri"] == "file:///tmp/evidence.md"

    db = sqlite3.connect(Path(tmp_path) / "data" / "control-plane.db")
    row = db.execute(
        "SELECT status, evidence_uri, diff_summary, completed_at FROM loop_runs"
        " WHERE run_id = ?",
        (run["run_id"],),
    ).fetchone()
    db.close()
    assert row is not None
    assert row[0] == "pass"
    assert row[1] == "file:///tmp/evidence.md"
    assert row[2] == "+42 -7"
    assert row[3] is not None


def test_loop_callback_revives_run_from_sqlite_after_restart(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    run = c.post("/api/v1/loop/run", json=_run_payload()).json()
    loops.reset_loops_for_tests()  # simulate process restart wiping memory

    r = c.post(
        "/api/v1/loop/callback",
        json={"run_id": run["run_id"], "status": "fail", "diff_summary": "boom"},
    )

    assert r.status_code == 200
    assert c.get("/loop/status").json()["active_loops"][0]["status"] == "fail"


def test_loop_callback_unknown_run_id_returns_404(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    r = c.post(
        "/api/v1/loop/callback", json={"run_id": "nope", "status": "pass"}
    )

    assert r.status_code == 404


def test_loop_run_missing_required_field_returns_422(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    payload = _run_payload()
    del payload["issue_ref"]

    r = c.post("/api/v1/loop/run", json=payload)

    assert r.status_code == 422


def test_loop_run_missing_executable_returns_400(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, executable="no-such-loop-bin-xyz", fake_which=False)

    r = c.post("/api/v1/loop/run", json=_run_payload())

    assert r.status_code == 400
    assert "no-such-loop-bin-xyz" in r.json()["detail"]


def test_loop_callback_invalid_status_returns_422(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    r = c.post(
        "/api/v1/loop/callback", json={"run_id": "x", "status": "exploded"}
    )

    assert r.status_code == 422


def test_loop_endpoints_are_not_exposed_in_openwebui_spec(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    spec = c.get("/openwebui/openapi.json").json()

    paths = spec["paths"]
    assert "/api/v1/loop/run" not in paths
    assert "/api/v1/loop/callback" not in paths
    assert "/loop/status" not in paths
    operations = {
        op[method].get("operationId")
        for op in paths.values()
        for method in op
    }
    assert "internal_loop_run" not in operations
    assert "internal_loop_callback" not in operations
    assert "internal_loop_status" not in operations
