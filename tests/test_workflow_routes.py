"""Workflow endpoints: all 6 return 200, background task really runs,
cancel really cancels, approve records decisions / reject halts.

NOTE: TestClient must be used as a context manager here — the plain mode
spawns a fresh event loop per request and would silently drop the runner's
background task between requests.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from workflow_testkit import (  # noqa: F401
    WF_EXEC_ID_RE,
    trigger_for,
    wf_env,
    write_workflow,
)

from personal_ai_control_plane.api import app
from personal_ai_control_plane.workflow import steps

SLOW_YAML = """
name: slow-demo
version: 1
steps:
  - id: slow
    type: slow_sleep
    inputs: {seconds: 2}
    timeout_seconds: 30
  - id: notify
    type: notify
    timeout_seconds: 5
"""


def _register_slow_step() -> None:
    async def slow_sleep(ctx):
        await asyncio.sleep(float(ctx.inputs.get("seconds", 2)))
        return {"slept": True}

    steps.register_step("slow_sleep", slow_sleep)


def _poll(client: TestClient, exec_id: str, predicate, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get(f"/api/v1/workflow/{exec_id}").json()
        if predicate(last):
            return last
        time.sleep(0.05)
    pytest.fail(f"condition not met within {timeout}s: {last}")


# ---- listing + definition endpoints ---------------------------------------

def test_list_workflows_returns_200(wf_env):  # noqa: F811
    with TestClient(app) as client:
        body = client.get("/api/v1/workflows").json()
    assert any(w["name"] == "java-ut-repair" for w in body["workflows"])
    entry = next(w for w in body["workflows"] if w["name"] == "java-ut-repair")
    assert "on_user_message" in entry["triggers_summary"]


def test_get_workflow_definition_returns_200(wf_env):  # noqa: F811
    with TestClient(app) as client:
        resp = client.get("/api/v1/workflows/java-ut-repair")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "java-ut-repair"
    assert "steps:" in body["yaml"]
    assert len(body["steps"]) == 8


def test_get_unknown_workflow_404(wf_env):  # noqa: F811
    with TestClient(app) as client:
        assert client.get("/api/v1/workflows/nope").status_code == 404


# ---- start + background runner --------------------------------------------

def test_start_runs_workflow_to_completion_in_background(wf_env):  # noqa: F811
    with TestClient(app) as client:
        resp = client.post("/api/v1/workflow/start", json={
            "workflow_name": "java-ut-repair",
            "trigger_context": trigger_for(wf_env["project"]),
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert WF_EXEC_ID_RE.match(body["execution_id"])
        assert body["status"] in ("PENDING", "RUNNING")

        final = _poll(client, body["execution_id"],
                      lambda b: b["status"] == "COMPLETED")
    assert all(sr["status"] == "DONE" for sr in final["step_results"])
    assert final["context"]["forgeloop"]["execution_id"]


def test_start_rejects_non_matching_trigger(wf_env):  # noqa: F811
    with TestClient(app) as client:
        resp = client.post("/api/v1/workflow/start", json={
            "workflow_name": "java-ut-repair",
            "trigger_context": {"source": "open_webui", "user_message": "今天天气如何"},
        })
    assert resp.status_code == 400


def test_start_unknown_workflow_404(wf_env):  # noqa: F811
    with TestClient(app) as client:
        resp = client.post("/api/v1/workflow/start", json={
            "workflow_name": "ghost", "trigger_context": {"user_message": "UT"},
        })
    assert resp.status_code == 404


def test_get_instance_404_for_unknown_execution(wf_env):  # noqa: F811
    with TestClient(app) as client:
        assert client.get("/api/v1/workflow/wf-20990101-000000").status_code == 404


# ---- approve / cancel -------------------------------------------------------

def test_approve_records_decision_and_returns_200(wf_env):  # noqa: F811
    write_workflow(wf_env["root"], "slow-demo", SLOW_YAML)
    _register_slow_step()
    with TestClient(app) as client:
        exec_id = client.post("/api/v1/workflow/start", json={
            "workflow_name": "slow-demo", "trigger_context": {"user_message": "UT 挂了"},
        }).json()["execution_id"]

        resp = client.post(f"/api/v1/workflow/{exec_id}/approve",
                           json={"decision": "approve", "reason": "plan 看起来没问题"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["context"]["approval"]["decision"] == "approve"

        final = _poll(client, exec_id, lambda b: b["status"] == "COMPLETED")
    assert final["context"]["approval"]["reason"] == "plan 看起来没问题"


def test_reject_halts_workflow(wf_env):  # noqa: F811
    write_workflow(wf_env["root"], "slow-demo", SLOW_YAML)
    _register_slow_step()
    with TestClient(app) as client:
        exec_id = client.post("/api/v1/workflow/start", json={
            "workflow_name": "slow-demo", "trigger_context": {"user_message": "UT 挂了"},
        }).json()["execution_id"]

        resp = client.post(f"/api/v1/workflow/{exec_id}/approve",
                           json={"decision": "reject", "reason": "target 不对"})
        assert resp.status_code == 200, resp.text

        final = _poll(client, exec_id,
                      lambda b: b["step_results"][1]["status"] == "SKIPPED")
    assert final["status"] == "REJECTED"


def test_cancel_moves_instance_to_cancelled(wf_env):  # noqa: F811
    write_workflow(wf_env["root"], "slow-demo", SLOW_YAML)
    _register_slow_step()
    with TestClient(app) as client:
        exec_id = client.post("/api/v1/workflow/start", json={
            "workflow_name": "slow-demo", "trigger_context": {"user_message": "UT 挂了"},
        }).json()["execution_id"]

        resp = client.post(f"/api/v1/workflow/{exec_id}/cancel", json={"reason": "手动取消"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "CANCELLED"

        final = _poll(client, exec_id,
                      lambda b: b["step_results"][1]["status"] == "SKIPPED")
        assert final["status"] == "CANCELLED"

        # cancelling twice / after terminal state -> 409
        again = client.post(f"/api/v1/workflow/{exec_id}/cancel", json={"reason": "again"})
    assert again.status_code == 409
