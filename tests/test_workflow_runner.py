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
from personal_ai_control_plane.workflow.runner import create_instance, run_workflow
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


def test_full_java_ut_repair_completes(wf_env):  # noqa: F811
    instance = _run("java-ut-repair", trigger_for(wf_env["project"]))

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
