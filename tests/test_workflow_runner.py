"""Workflow runner: full java-ut-repair chain (real discovery+forgeloop over
the app's own HTTP endpoints), failure/timeout semantics, and trigger-level
idempotency through /workflow/start."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from workflow_testkit import (  # noqa: F401
    LOOP_EXEC_ID_RE,
    WF_EXEC_ID_RE,
    trigger_for,
    wf_env,
)

from personal_ai_control_plane.api import app
from personal_ai_control_plane.workflow import steps
from personal_ai_control_plane.workflow.runner import create_instance, record_approval, run_workflow
from personal_ai_control_plane.workflow.state import (
    StepStatus,
    WorkflowStatus,
    transition,
)

FAILING_TEST = "com.demo.X#testLogin"


def _run(workflow_name: str, trigger: dict):
    instance = create_instance(workflow_name, trigger)
    transition(instance, WorkflowStatus.RUNNING)
    return asyncio.run(run_workflow(instance))


def _approve_and_resume(instance):
    """人工批准 + 续跑（与路由同源：runner.record_approval）。"""
    assert record_approval(instance, "approve", "test")
    return asyncio.run(run_workflow(instance))


def test_full_java_ut_repair_completes(wf_env):  # noqa: F811
    instance = _run("java-ut-repair", trigger_for(wf_env["project"]))

    # 审批闸门回归（蔚 2026-10-26 验收口径）：未审批 → forgeloop 绝不 RUNNING、不产生 run_id
    assert instance.status is WorkflowStatus.WAITING_FOR_APPROVAL
    gate = next(sr for sr in instance.step_results if sr.step_id == "forgeloop")
    assert gate.status is StepStatus.PENDING
    assert "forgeloop" not in instance.context

    instance = _approve_and_resume(instance)

    assert instance.status is WorkflowStatus.COMPLETED
    assert [sr.status for sr in instance.step_results] == [StepStatus.DONE] * 8

    discovery = instance.context["discovery"]
    assert discovery["recommended_target"] == FAILING_TEST
    assert discovery["failing_tests"] == [FAILING_TEST]

    # outputs flow into the next steps' rendered inputs
    issue = next(sr for sr in instance.step_results if sr.step_id == "issue")
    assert issue.inputs["title_template"] == f"修复 {FAILING_TEST}"
    sync = next(sr for sr in instance.step_results if sr.step_id == "sync_multica")
    assert sync.inputs["issue_id"] == "stub-issue"

    forgeloop = instance.context["forgeloop"]
    assert LOOP_EXEC_ID_RE.match(forgeloop["execution_id"]), forgeloop
    assert forgeloop["result"] == "running"


def test_step_outputs_reach_following_steps(wf_env):  # noqa: F811
    instance = _run("java-ut-repair", trigger_for(wf_env["project"]))
    instance = _approve_and_resume(instance)
    approval = next(sr for sr in instance.step_results if sr.step_id == "approval")
    assert "java-ut-fixer" in approval.inputs["plan_template"]
    assert FAILING_TEST in approval.inputs["plan_template"]


def test_step_failure_fails_instance_and_skips_rest(wf_env):  # noqa: F811
    async def exploding(ctx):
        raise RuntimeError("KB is down")

    steps.register_step("knowledge", exploding)
    try:
        instance = _run("java-ut-repair", trigger_for(wf_env["project"]))
    finally:
        steps.register_step("knowledge", steps.knowledge_step)

    assert instance.status is WorkflowStatus.FAILED
    statuses = [sr.status for sr in instance.step_results]
    assert statuses == [StepStatus.DONE, StepStatus.FAILED] + [StepStatus.SKIPPED] * 6
    assert instance.step_results[1].error and "KB is down" in instance.step_results[1].error


def test_step_timeout_fails_instance(wf_env):  # noqa: F811
    from workflow_testkit import write_workflow

    write_workflow(wf_env["root"], "slow", """
name: slow
version: 1
steps:
  - id: sleep
    type: slow_sleep
    inputs: {seconds: 5}
    timeout_seconds: 1
  - id: notify
    type: notify
    timeout_seconds: 5
""")

    async def slow_sleep(ctx):
        await asyncio.sleep(float(ctx.inputs.get("seconds", 5)))
        return {}

    steps.register_step("slow_sleep", slow_sleep)
    try:
        instance = _run("slow", trigger_for(wf_env["project"]))
    finally:
        del steps.STEP_REGISTRY["slow_sleep"]

    assert instance.status is WorkflowStatus.FAILED
    assert "timed out" in (instance.step_results[0].error or "")
    assert instance.step_results[1].status is StepStatus.SKIPPED


def test_optional_failure_does_not_fail_instance(wf_env):  # noqa: F811
    from workflow_testkit import write_workflow

    write_workflow(wf_env["root"], "tolerant", """
name: tolerant
version: 1
steps:
  - id: flaky
    type: exploding_step
    optional: true
    timeout_seconds: 5
  - id: notify
    type: notify
    timeout_seconds: 5
""")

    async def exploding(ctx):
        raise ValueError("boom")

    steps.register_step("exploding_step", exploding)
    try:
        instance = _run("tolerant", trigger_for(wf_env["project"]))
    finally:
        del steps.STEP_REGISTRY["exploding_step"]

    assert instance.status is WorkflowStatus.COMPLETED
    assert instance.step_results[0].status is StepStatus.FAILED
    assert instance.step_results[1].status is StepStatus.DONE


def test_same_trigger_restart_returns_same_execution_id(wf_env):  # noqa: F811
    client = TestClient(app)
    trigger = trigger_for(wf_env["project"])

    first = client.post("/api/v1/workflow/start", json={
        "workflow_name": "java-ut-repair", "trigger_context": trigger,
    })
    assert first.status_code == 200, first.text
    exec_id = first.json()["execution_id"]
    assert WF_EXEC_ID_RE.match(exec_id)

    second = client.post("/api/v1/workflow/start", json={
        "workflow_name": "java-ut-repair", "trigger_context": trigger,
    })
    assert second.status_code == 200, second.text
    assert second.json()["execution_id"] == exec_id
    assert second.json().get("idempotent_reuse") is True


def test_make_client_ignores_env_proxy(monkeypatch) -> None:
    """P1 回归（43 天债）：系统代理环境下 workflow httpx client 不读 HTTP(S)_PROXY。

    走代理的开发机（如 127.0.0.1:7892）在 discovery step 必 502，
    trust_env=False 后 client 不挂任何 proxy mount。
    """
    from personal_ai_control_plane.workflow import steps

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7892")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7892")
    client = steps._make_client()
    try:
        assert len(client._mounts) == 0, "httpx client 不应挂载环境代理"
    finally:
        import asyncio

        asyncio.run(client.aclose())


def test_terminal_failure_same_trigger_restarts_new_execution(wf_env):
    """蔚 2026-11-17 N1：FAILED 终态不再吞重试——同 trigger 重发直接重开新执行。

    确定性复刻：一步必败的 INTOLERANT 测试流（无审批闸门、无外部依赖——
    java-ut-repair 版本会停在审批后外部步上超时，教训入注）。RUNNING 态
    复用行为由既有 test_same_trigger_restart_returns_same_execution_id 守护。
    """
    from workflow_testkit import write_workflow

    write_workflow(wf_env["root"], "n1-fatal", """
name: n1-fatal
version: 1
steps:
  - id: boom
    type: n1_fail_step
    timeout_seconds: 5
""")

    async def n1_fail(ctx):
        raise steps.StepError("N1 deterministic failure")

    steps.register_step("n1_fail_step", n1_fail)
    try:
        client = TestClient(app)
        trigger = trigger_for(wf_env["project"]) | {"n1_case": "terminal-retry"}
        first = client.post("/api/v1/workflow/start", json={
            "workflow_name": "n1-fatal", "trigger_context": trigger,
        })
        assert first.status_code == 200, first.text
        first_id = first.json()["execution_id"]

        import time as _t
        deadline = _t.time() + 15
        status = None
        while _t.time() < deadline:
            status = client.get(f"/api/v1/workflow/{first_id}").json()["status"]
            if status in ("FAILED", "COMPLETED"):
                break
            _t.sleep(0.2)
        assert status == "FAILED", f"预期 FAILED 实测 {status}"

        second = client.post("/api/v1/workflow/start", json={
            "workflow_name": "n1-fatal", "trigger_context": trigger,
        })
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["execution_id"] != first_id, "终态重开必须给新 execution_id"
        assert body.get("restarted_from") == first_id
        assert body.get("idempotent_reuse") is None
    finally:
        del steps.STEP_REGISTRY["n1_fail_step"]


def test_discovery_step_error_message_has_guidance(monkeypatch):
    """蔚 2026-11-17 N4：无失败用例时的指引句 + 不留悬空冒号。"""
    import types

    from personal_ai_control_plane.workflow import steps as wf_steps

    async def fake_post(path, payload):
        return {"recommended_target": None, "failure_summary": ""}

    monkeypatch.setattr(wf_steps, "_post", fake_post)
    ctx = types.SimpleNamespace(inputs={"user_message": "修测试", "workspace_root": "/tmp"})
    try:
        import asyncio

        asyncio.run(wf_steps.discovery_step(ctx))
        raise AssertionError("discovery_step 应抛 StepError")
    except wf_steps.StepError as err:
        msg = str(err)
        assert "surefire-reports" in msg and "mvn test" in msg
        assert ": —" not in msg and not msg.rstrip("—需要").endswith(": "), msg
