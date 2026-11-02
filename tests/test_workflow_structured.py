# tests/test_workflow_structured.py - 蔚 2026-11-02 工单：
# P2-5 结构化错误（error_code/http_status/error_detail）+ P3-3 list 最近执行摘要 + P3-4 preflight
from __future__ import annotations

import asyncio
from unittest import mock

from fastapi.testclient import TestClient
from workflow_testkit import (  # noqa: F401
    trigger_for,
    wf_env,
)

from personal_ai_control_plane.api import app
from personal_ai_control_plane.workflow import steps
from personal_ai_control_plane.workflow.runner import create_instance, run_workflow
from personal_ai_control_plane.workflow.state import (
    StepStatus,
    WorkflowStatus,
    transition,
)
from personal_ai_control_plane.workflow.steps import StepError


def _run_to_failure(wf_env):  # noqa: F811
    instance = create_instance("java-ut-repair", trigger_for(wf_env["project"]))
    transition(instance, WorkflowStatus.RUNNING)
    return asyncio.run(run_workflow(instance))


def test_steperror_upstream_http_carries_structured_fields(wf_env):  # noqa: F811
    """蔚实测样本：forgeloop 上游 400 裸 JSON——三字段直读，不再从字符串挖。"""
    detail = '{"detail":"loop executable \'loop\' was not found on PATH (set AICP_LOOP_EXECUTABLE to override)"}'

    async def exploding(ctx):
        raise StepError(
            "POST /api/v1/execution/confirm -> 400: {trunc}",
            http_status=400,
            error_detail=detail,
        )

    steps.register_step("knowledge", exploding)
    try:
        instance = _run_to_failure(wf_env)
    finally:
        steps.register_step("knowledge", steps.knowledge_step)

    assert instance.status is WorkflowStatus.FAILED
    failed = instance.step_results[1]
    assert failed.status is StepStatus.FAILED
    assert failed.error_code == "UPSTREAM_HTTP"
    assert failed.http_status == 400
    assert "loop executable" in (failed.error_detail or "")
    # 兼容字符串保留（既有断言不破）
    assert failed.error is not None and failed.error.startswith("StepError:")


def test_steperror_without_http_is_step_logic(wf_env):  # noqa: F811
    async def exploding(ctx):
        raise StepError("discovery found no failing test to target: (empty)")

    steps.register_step("knowledge", exploding)
    try:
        instance = _run_to_failure(wf_env)
    finally:
        steps.register_step("knowledge", steps.knowledge_step)

    failed = instance.step_results[1]
    assert failed.error_code == "STEP_LOGIC"
    assert failed.http_status is None


def test_start_response_includes_preflight_warning_when_loop_missing(wf_env):  # noqa: F811
    with TestClient(app) as client:
        with mock.patch(
            "personal_ai_control_plane.workflow_routes.shutil.which", return_value=None
        ):
            resp = client.post(
                "/api/v1/workflow/start",
                json={
                    "workflow_name": "java-ut-repair",
                    "trigger_context": trigger_for(wf_env["project"]),
                },
            )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "warning" in body
    assert "forgeloop" in body["warning"]
    assert "AICP_LOOP_EXECUTABLE" in body["warning"]


def test_start_without_forgeloop_gives_no_warning(wf_env):  # noqa: F811
    """无 forgeloop 步的工作流不该被 preflight 误伤（慢流闸门版已有 gate 步）。"""
    from workflow_testkit import write_workflow

    write_workflow(
        wf_env["root"],
        "no-loop-demo",
        """
name: no-loop-demo
version: 1
steps:
  - id: slow
    type: slow_sleep
    inputs: {seconds: 2}
    timeout_seconds: 30
  - id: gate
    type: approval
    timeout_seconds: 300
""",
    )

    async def slow_sleep(ctx):
        import asyncio as _asyncio

        await _asyncio.sleep(0.1)
        return {"slept": True}

    steps.register_step("slow_sleep", slow_sleep)
    try:
        with TestClient(app) as client:
            with mock.patch(
                "personal_ai_control_plane.workflow_routes.shutil.which",
                return_value=None,
            ):
                resp = client.post(
                    "/api/v1/workflow/start",
                    json={
                        "workflow_name": "no-loop-demo",
                        "trigger_context": {"user_message": "UT 挂了"},
                    },
                )
        assert resp.status_code == 200, resp.text
        assert "warning" not in resp.json()
    finally:
        pass


def test_workflows_list_includes_latest_run(wf_env):  # noqa: F811
    with TestClient(app) as client:
        started = client.post(
            "/api/v1/workflow/start",
            json={
                "workflow_name": "java-ut-repair",
                "trigger_context": trigger_for(wf_env["project"]),
            },
        ).json()
        resp = client.get("/api/v1/workflows")
    entry = next(
        w for w in resp.json()["workflows"] if w["name"] == "java-ut-repair"
    )
    assert entry["latest_run"] is not None
    assert entry["latest_run"]["execution_id"] == started["execution_id"]
    assert entry["latest_run"]["status"] in (
        "PENDING",
        "RUNNING",
        "WAITING_FOR_APPROVAL",
    )
