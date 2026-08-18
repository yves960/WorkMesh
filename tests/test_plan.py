"""Task 3: execution plan + confirm flow (the mandatory confirmation gate).

Covers: plan/execution id formats, user_overrides allow-list, the 3
estimated-minutes tiers, one-shot confirm (+ repeat 404), execution_id reuse
and fallback, the _GUARD defense-in-depth, the 410 on the old direct-run
route, and an end-to-end discovery -> plan -> confirm with a mocked spawn.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from personal_ai_control_plane import execution_routes, loops
from personal_ai_control_plane.api import app
from personal_ai_control_plane.errors import ControlPlaneError
from personal_ai_control_plane.models import LoopRunRequest

PLAN_ID_RE = re.compile(r"^plan-\d{8}-[0-9a-f]{6}$")
EXEC_ID_RE = re.compile(r"^exec-\d{8}-[0-9a-f]{6}$")


class RecordingPopen:
    """Mocked subprocess.Popen: records argv, never spawns anything.

    Also implements the context-manager/communicate surface so the REAL
    `subprocess.run` (used internally by discovery's git probe) keeps working
    with the fake installed on the shared `subprocess` module.
    """

    last_cmd: list[str] | None = None

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.args = cmd
        self.pid = 4242
        self.returncode = 1
        RecordingPopen.last_cmd = list(cmd)

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return self.returncode

    def communicate(self, input=None, timeout=None):
        return (b"", b"")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _setup(
    monkeypatch, tmp_path, *, executable: str = "loop", fake_which: bool = True
) -> TestClient:
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    monkeypatch.setattr(loops.subprocess, "Popen", RecordingPopen)
    if fake_which:
        monkeypatch.setattr(loops.shutil, "which", lambda _n: "/usr/local/bin/loop")
    monkeypatch.setenv("AICP_LOOP_EXECUTABLE", executable)
    loops.reset_loops_for_tests()
    execution_routes.reset_plans_for_tests()
    RecordingPopen.last_cmd = None
    return TestClient(app)


def _discovery(**overrides) -> dict[str, Any]:
    base: dict[str, Any] = {
        "project_path": "/tmp/demo-java-ut",
        "project_name": "demo-java-ut",
        "build_tool": "maven",
        "repo_url": None,
        "language": "java",
        "failing_tests": ["com.demo.UserServiceTest#testLogin"],
        "failure_summary": "com.demo.UserServiceTest#testLogin FAILED",
        "recommended_target": "com.demo.UserServiceTest#testLogin",
        "recommended_profile": "java-ut-fixer",
        "discovery_confidence": 0.9,
    }
    base.update(overrides)
    return base


def _plan(c: TestClient, discovery=None, overrides=None):
    payload: dict[str, Any] = {
        "discovery": _discovery() if discovery is None else discovery,
    }
    if overrides is not None:
        payload["user_overrides"] = overrides
    return c.post("/api/v1/execution/plan", json=payload)


def _confirm(c: TestClient, plan_id: str):
    return c.post("/api/v1/execution/confirm", json={"plan_id": plan_id})


# ---- plan creation ----------------------------------------------------------


def test_plan_creates_plan_and_execution_ids(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)

    r = _plan(c)

    assert r.status_code == 200, r.text
    body = r.json()
    assert PLAN_ID_RE.match(body["plan_id"]), body["plan_id"]
    assert EXEC_ID_RE.match(body["execution_id"]), body["execution_id"]
    assert body["project"] == "demo-java-ut"
    assert body["project_path"] == "/tmp/demo-java-ut"
    assert body["repo"] is None
    assert body["profile"] == "java-ut-fixer"
    assert body["target"] == "com.demo.UserServiceTest#testLogin"
    assert body["related_issue"] is None
    assert body["estimated_minutes"] == 2
    assert body["requires_confirmation"] is True
    assert body["created_at"]
    # Creating a plan must not spawn anything.
    assert RecordingPopen.last_cmd is None


def test_get_plan_returns_stored_plan(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)
    plan = _plan(c).json()

    r = c.get(f"/api/v1/execution/plan/{plan['plan_id']}")

    assert r.status_code == 200
    assert r.json() == plan


def test_get_unknown_plan_returns_404(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)

    r = c.get("/api/v1/execution/plan/plan-19700101-nope00")

    assert r.status_code == 404


# ---- user_overrides ---------------------------------------------------------


def test_user_overrides_all_three_legal_fields(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)

    r = _plan(
        c,
        overrides={
            "target": "com.demo.OrderServiceTest#testCalc",
            "profile": "sast-fixer",
            "issue_ref": "MUL-42",
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "com.demo.OrderServiceTest#testCalc"
    assert body["profile"] == "sast-fixer"
    assert body["related_issue"] == "MUL-42"


def test_user_overrides_illegal_field_returns_400(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)

    r = _plan(c, overrides={"target": "com.demo.A", "max_iterations": 99})

    assert r.status_code == 400
    assert "max_iterations" in r.json()["detail"]


def test_user_overrides_illegal_project_override_returns_400(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)

    r = _plan(c, overrides={"project_path": "/etc"})

    assert r.status_code == 400
    assert "project_path" in r.json()["detail"]


# ---- target requirement + estimates ----------------------------------------


def test_plan_without_target_returns_400(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)

    r = _plan(c, discovery=_discovery(recommended_target="", failing_tests=[]))

    assert r.status_code == 400
    assert "target" in r.json()["detail"]


def test_plan_without_target_rescued_by_override(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)

    r = _plan(
        c,
        discovery=_discovery(recommended_target="", failing_tests=[]),
        overrides={"target": "com.demo.UserServiceTest"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["target"] == "com.demo.UserServiceTest"


def test_estimated_minutes_three_tiers(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)
    cases = [
        ("com.demo.UserServiceTest#testLogin", 2),  # method -> 2 min
        ("com.demo.UserServiceTest", 5),  # class   -> 5 min
        ("com.demo", 10),  # package -> 10 min
        ("com.demo.*", 10),  # wildcard package -> 10 min
    ]
    for target, minutes in cases:
        r = _plan(c, overrides={"target": target})
        assert r.status_code == 200, r.text
        assert r.json()["estimated_minutes"] == minutes, target


# ---- confirm ----------------------------------------------------------------


def test_confirm_launches_and_reuses_plan_execution_id(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)
    plan = _plan(c).json()

    r = _confirm(c, plan["plan_id"])

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "running"
    assert body["run_id"]
    assert body["execution_id"] == plan["execution_id"]  # reuse, not regenerate
    argv = RecordingPopen.last_cmd or []
    assert argv[argv.index("--execution-id") + 1] == plan["execution_id"]
    assert argv[argv.index("--target") + 1] == "com.demo.UserServiceTest#testLogin"
    assert argv[argv.index("--max-iterations") + 1] == "1"  # dry_run single pass


def test_confirm_twice_returns_404(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)
    plan = _plan(c).json()
    assert _confirm(c, plan["plan_id"]).status_code == 200

    second = _confirm(c, plan["plan_id"])
    # one-shot: the consumed plan is gone (also from GET)
    assert second.status_code == 404
    assert c.get(f"/api/v1/execution/plan/{plan['plan_id']}").status_code == 404
    assert RecordingPopen.last_cmd is not None
    assert len(loops.active_loops()) == 1  # exactly one run ever launched


def test_confirm_unknown_plan_returns_404(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)

    r = _confirm(c, "plan-19700101-nope00")

    assert r.status_code == 404


def test_confirm_execution_id_fallback(monkeypatch, tmp_path):
    # If a plan somehow carries no execution_id, start_loop's own fallback
    # must still generate a well-formed one (execution_id 复用兜底).
    c = _setup(monkeypatch, tmp_path)
    plan = _plan(c).json()
    stored = execution_routes._PLANS[plan["plan_id"]]
    stored.execution_id = ""
    stored.run_request.execution_id = None

    r = _confirm(c, plan["plan_id"])

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["execution_id"]
    assert EXEC_ID_RE.match(body["execution_id"])
    assert body["execution_id"] != plan["execution_id"]


def test_confirm_with_broken_executable_400_and_plan_consumed(monkeypatch, tmp_path):
    c = _setup(
        monkeypatch, tmp_path, executable="no-such-loop-bin-xyz", fake_which=False
    )
    plan = _plan(c).json()

    r = _confirm(c, plan["plan_id"])

    assert r.status_code == 400
    assert "no-such-loop-bin-xyz" in r.json()["detail"]
    # The plan was consumed: a retry cannot silently relaunch later.
    assert _confirm(c, plan["plan_id"]).status_code == 404


# ---- guard + old route ------------------------------------------------------


def test_start_loop_outside_guard_refuses_to_spawn(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    with pytest.raises(ControlPlaneError, match="confirmation"):
        loops.start_loop(
            LoopRunRequest(
                workspace_id="ws-1",
                project_path="/tmp/demo",
                profile_name="java-ut-fixer",
                target="com.demo.A",
                dry_run=True,
            )
        )
    assert RecordingPopen.last_cmd is None  # never spawned


def test_old_loop_run_route_returns_410(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)
    payload = {
        "workspace_id": "ws-1",
        "project_path": "/tmp/demo-java-ut",
        "profile_name": "java-ut-fixer",
        "target": "com.demo.UserServiceTest",
        "dry_run": True,
    }

    r = c.post("/api/v1/loop/run", json=payload)

    assert r.status_code == 410
    assert "/api/v1/execution/confirm" in r.json()["detail"]
    assert RecordingPopen.last_cmd is None  # nothing spawned, ever


# ---- end to end: discovery -> plan -> confirm -------------------------------


def _make_demo_project(tmp_path: Path) -> Path:
    project = tmp_path / "demo-java-ut"
    (project / "src").mkdir(parents=True)
    (project / "pom.xml").write_text(
        "<project><groupId>com.demo</groupId>"
        "<artifactId>demo-java-ut</artifactId><version>0.0.1</version></project>"
    )
    reports = project / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-com.demo.UserServiceTest.txt").write_text(
        "Tests run: 3, Failures: 1\n"
        "com.demo.UserServiceTest#testLogin FAILED\n"
        "NullPointerException at line 42\n"
    )
    return project


def test_end_to_end_discovery_plan_confirm(monkeypatch, tmp_path):
    c = _setup(monkeypatch, tmp_path)
    _make_demo_project(tmp_path)

    discovery = c.post(
        "/api/v1/discovery",
        json={"user_message": "UT 挂了，帮我修", "workspace_root": str(tmp_path)},
    )
    assert discovery.status_code == 200, discovery.text

    plan = c.post("/api/v1/execution/plan", json={"discovery": discovery.json()})
    assert plan.status_code == 200, plan.text
    plan_body = plan.json()
    assert plan_body["target"] == "com.demo.UserServiceTest#testLogin"
    assert plan_body["estimated_minutes"] == 2

    confirmed = _confirm(c, plan_body["plan_id"])
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["execution_id"] == plan_body["execution_id"]
    assert body["status"] == "running"
    argv = RecordingPopen.last_cmd or []
    assert argv[:2] == ["loop", "run"]
    assert argv[argv.index("--target") + 1] == "com.demo.UserServiceTest#testLogin"
