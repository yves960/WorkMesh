#!/usr/bin/env python3
"""脱离 pytest 的 workflow 冒烟：start → WAITING_FOR_APPROVAL → approve → 终态。

复用 tests/workflow_testkit.py 的隔离与 mock（ASGI 自环 transport + FakePopen），
不依赖任何外部服务与 pytest，直接 `python3 scripts/smoke_workflow.py` 演示/排障。
退出码：0 = 全程走通到 DONE；1 = 中途失败或超时。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

import httpx  # noqa: E402

import workflow_testkit as testkit  # noqa: E402
from personal_ai_control_plane.api import app  # noqa: E402
from personal_ai_control_plane.workflow import loader, steps  # noqa: E402

TERMINAL = ("DONE", "FAILED", "CANCELLED")


def _prepare(tmp: str) -> Path:
    """镜像 wf_env fixture 的隔离与 mock，只是不依赖 pytest。"""
    root = Path(tmp) / "root"
    root.mkdir()
    yaml_text = (REPO / "workflows" / "java-ut-repair.yaml").read_text(encoding="utf-8")
    testkit.write_workflow(root, "java-ut-repair", yaml_text)
    workspace = Path(tmp) / "workspace"
    workspace.mkdir()
    project = testkit.make_project(workspace)

    os.environ["AICP_ROOT"] = str(root)
    steps._make_client = lambda: httpx.AsyncClient(  # noqa: SLF001
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )
    testkit.loops.subprocess.Popen = testkit.FakePopen  # type: ignore[attr-defined]
    testkit.loops.shutil.which = lambda _n: "/usr/local/bin/loop"
    os.environ["AICP_LOOP_EXECUTABLE"] = "loop"
    loader.clear_loader_cache()
    testkit.loops.reset_loops_for_tests()
    testkit.execution_routes.reset_plans_for_tests()
    testkit.reset_workflow_routes_for_tests()
    return project


async def _poll(client: httpx.AsyncClient, execution_id: str, want: str, rounds: int = 40) -> dict:
    status: dict = {}
    for _ in range(rounds):
        status = (await client.get(f"/api/v1/workflow/{execution_id}")).json()
        print(f"  status={status['status']} current_step={status['current_step']}")
        if status["status"] == want or status["status"] in TERMINAL:
            return status
        await asyncio.sleep(0.25)
    return status


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        project = _prepare(tmp)
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://smoke") as client:
                trigger = testkit.trigger_for(project)
                print("== POST /api/v1/workflow/start ==")
                resp = await client.post(
                    "/api/v1/workflow/start",
                    json={"workflow_name": "java-ut-repair", "trigger_context": trigger},
                )
                print(f"HTTP {resp.status_code}")
                print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
                if resp.status_code != 200:
                    return 1
                execution_id = resp.json()["execution_id"]

                print("== poll → WAITING_FOR_APPROVAL ==")
                status = await _poll(client, execution_id, "WAITING_FOR_APPROVAL")
                if status.get("status") != "WAITING_FOR_APPROVAL":
                    print("[smoke] ERROR: 未到达 WAITING_FOR_APPROVAL")
                    return 1

                print("== POST /api/v1/workflow/{id}/approve ==")
                approve = await client.post(
                    f"/api/v1/workflow/{execution_id}/approve",
                    json={"decision": "approve", "reason": "smoke run"},
                )
                print(f"HTTP {approve.status_code}")
                if approve.status_code != 200:
                    print(json.dumps(approve.json(), ensure_ascii=False, indent=2))
                    return 1

                print("== poll → 终态 ==")
                status = await _poll(client, execution_id, "DONE")
                final = {
                    key: status.get(key)
                    for key in ("execution_id", "workflow_name", "status", "current_step")
                }
                final["steps"] = [
                    {"id": sr.get("step_id"), "status": sr.get("status")}
                    for sr in status.get("step_results", [])
                ]
                print(json.dumps(final, ensure_ascii=False, indent=2))
                ok = status.get("status") == "DONE"
                print(f"[smoke] {'OK: 全程走通' if ok else 'FAIL: 终态=' + str(status.get('status'))}")
                return 0 if ok else 1
        finally:
            loader.clear_loader_cache()
            testkit.reset_workflow_routes_for_tests()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
