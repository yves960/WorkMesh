"""Task 1: issue_ref and target are decoupled.

- target: REQUIRED, the real ForgeLoop execution goal (--target).
- issue_ref: optional Multica metadata stored on ExecutionRecord only;
  it must never appear in the spawned argv.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from personal_ai_control_plane import execution_routes, loops
from personal_ai_control_plane.api import app
from personal_ai_control_plane.errors import ControlPlaneError, MissingTargetError
from personal_ai_control_plane.models import LoopRunRequest


class RecordingPopen:
    """Mocked subprocess.Popen: records argv, never spawns anything."""

    last_cmd: list[str] | None = None

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.pid = 4242
        RecordingPopen.last_cmd = list(cmd)

    def wait(self, timeout=None):
        return 0


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    monkeypatch.setattr(loops.subprocess, "Popen", RecordingPopen)
    monkeypatch.setattr(loops.shutil, "which", lambda _n: "/usr/local/bin/loop")
    monkeypatch.setenv("AICP_LOOP_EXECUTABLE", "loop")
    loops.reset_loops_for_tests()
    execution_routes.reset_plans_for_tests()
    return TestClient(app)


def _payload(**overrides) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": "ws-1",
        "project_path": "~/projects/self/demo-java-ut",
        "profile_name": "java-ut-fixer",
        "target": "com.demo.UserServiceTest",
        "dry_run": True,
    }
    payload.update(overrides)
    return payload


def _db(tmp_path) -> sqlite3.Connection:
    return sqlite3.connect(Path(tmp_path) / "data" / "control-plane.db")


def _execution_row(tmp_path, execution_id: str) -> tuple:
    db = _db(tmp_path)
    row = db.execute(
        "SELECT target, issue_ref, status FROM execution_records"
        " WHERE execution_id = ?",
        (execution_id,),
    ).fetchone()
    db.close()
    return row


# --- required-field validation (Pydantic model layer) ----------------------


def test_loop_run_request_target_none_is_rejected():
    with pytest.raises(ValidationError):
        LoopRunRequest(**_payload(target=None))


def test_loop_run_request_target_missing_is_rejected():
    payload = _payload()
    del payload["target"]
    with pytest.raises(ValidationError):
        LoopRunRequest(**payload)


def test_loop_run_request_issue_ref_none_is_accepted():
    # Backwards compatibility: issue_ref=None must still validate.
    req = LoopRunRequest(**_payload(issue_ref=None))
    assert req.issue_ref is None
    assert req.target == "com.demo.UserServiceTest"


def test_validate_request_rejects_blank_target():
    with pytest.raises(MissingTargetError, match="target is required"):
        loops._validate_request(LoopRunRequest(**_payload(target="   ")))


def test_validate_request_accepts_target_without_issue_ref():
    loops._validate_request(LoopRunRequest(**_payload(issue_ref=None)))


def _start(c: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    """Plan -> confirm; returns launched run JSON."""
    discovery = {
        "project_path": payload["project_path"],
        "project_name": "demo-java-ut",
        "repo_url": None,
        "recommended_target": payload["target"],
        "recommended_profile": payload.get("profile_name") or "java-ut-fixer",
    }
    overrides: dict[str, Any] = {}
    if payload.get("issue_ref") is not None:
        overrides["issue_ref"] = payload["issue_ref"]
    plan = c.post(
        "/api/v1/execution/plan",
        json={"discovery": discovery, "user_overrides": overrides or None},
    )
    assert plan.status_code == 200, plan.text
    confirmed = c.post(
        "/api/v1/execution/confirm", json={"plan_id": plan.json()["plan_id"]}
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


# --- HTTP layer -------------------------------------------------------------


def test_old_loop_run_route_is_gone(monkeypatch, tmp_path):
    # Task 3: the direct-run route is disabled (410); target-required and
    # issue-ref-optional semantics live on at the plan endpoint + model layer.
    c = _client(monkeypatch, tmp_path)

    r = c.post("/api/v1/loop/run", json=_payload())

    assert r.status_code == 410


def test_old_loop_run_route_without_target_is_rejected(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    r = c.post("/api/v1/loop/run", json=_payload(target=None))

    assert r.status_code == 422  # schema layer still refuses a target-less run


# --- happy paths ------------------------------------------------------------


def test_target_only_run_succeeds_with_issue_ref_none(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    body = _start(c, _payload())  # no issue_ref at all

    assert body["execution_id"]
    assert body["status"] == "running"
    assert _execution_row(tmp_path, body["execution_id"]) == (
        "com.demo.UserServiceTest",
        None,
        "running",
    )


def test_target_and_issue_ref_both_persisted(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    body = _start(c, _payload(issue_ref="MUL-42"))

    assert body["execution_id"]
    assert _execution_row(tmp_path, body["execution_id"]) == (
        "com.demo.UserServiceTest",
        "MUL-42",
        "running",
    )


# --- argv transparency ------------------------------------------------------


@pytest.mark.parametrize("issue_ref", [None, "MUL-42", "issue-ref-xyz"])
def test_argv_carries_target_never_issue_ref(monkeypatch, tmp_path, issue_ref):
    c = _client(monkeypatch, tmp_path)

    body = _start(c, _payload(issue_ref=issue_ref, target="com.demo.OrderServiceTest"))

    assert body["status"] == "running"
    cmd = RecordingPopen.last_cmd or []
    assert cmd[cmd.index("--target") + 1] == "com.demo.OrderServiceTest"
    if issue_ref is not None:
        assert issue_ref not in cmd  # metadata must never reach the CLI
    assert "--issue-ref" not in cmd
    assert "--issue" not in cmd


def test_start_loop_missing_target_raises_before_spawn(monkeypatch):
    monkeypatch.setattr(loops.subprocess, "Popen", RecordingPopen)
    monkeypatch.setattr(loops.shutil, "which", lambda _n: "/usr/local/bin/loop")
    RecordingPopen.last_cmd = None  # class attr persists across tests
    loops.reset_loops_for_tests()

    with (
        pytest.raises(ControlPlaneError, match="target is required"),
        loops._GUARD(),
    ):
        loops.start_loop(LoopRunRequest(**_payload(target="  ")))
    assert RecordingPopen.last_cmd is None  # never spawned
    loops.reset_loops_for_tests()
