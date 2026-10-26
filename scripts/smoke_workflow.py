#!/usr/bin/env python3
"""脱离 pytest 的 workflow 冒烟：start → 审批闸门 → approve → COMPLETED。

2026-10-26 P0/P1-3 重写：审批闸门为真实语义（runner 在 approval /
requires_approval 步停住，实例转 WAITING_FOR_APPROVAL），本脚本按真实语义断言：
  1. start 后必然到达 WAITING_FOR_APPROVAL（approval 步暂停，不是自动放行）；
  2. 等待态下 forgeloop 绝不 RUNNING、context 无 forgeloop 输出（不产生 run_id）；
  3. approve 后跑到 COMPLETED，forgeloop 输出含 run_id（闸门放行后真执行）。
退出码：0 = 全程真实走通；1 = 任一断言失败（本脚本是验收凭证，必须自己先能绿）。
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

#: 实例级终态（蔚 10-26 勘误：DONE 是步级状态，实例级是这四个）
TERMINAL = ("COMPLETED", "FAILED", "REJECTED", "CANCELLED")


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
                if resp.status_code != 200:
                    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
                    return 1
                execution_id = resp.json()["execution_id"]

                print("== 1) poll → WAITING_FOR_APPROVAL（审批闸门真实存在） ==")
                waiting = await _poll(client, execution_id, "WAITING_FOR_APPROVAL")
                if waiting.get("status") != "WAITING_FOR_APPROVAL":
                    print(f"[smoke] FAIL: 未停在审批闸门，status={waiting.get('status')}")
                    return 1

                # 蔚的验收断言：未审批 → forgeloop 绝不 RUNNING、不产生 run_id
                gate = next(
                    (sr for sr in waiting.get("step_results", []) if sr.get("step_id") == "forgeloop"),
                    {},
                )
                if gate.get("status") != "PENDING":
                    print(f"[smoke] FAIL: 等待态 forgeloop 应为 PENDING，实际 {gate.get('status')}")
                    return 1
                if "forgeloop" in (waiting.get("context") or {}):
                    print("[smoke] FAIL: 等待态 context 已出现 forgeloop 输出（闸门被绕过！）")
                    return 1
                print("[smoke] OK: 未审批时 forgeloop 保持 PENDING，未产生 run_id")

                print("== 2) POST /approve {decision: approve} ==")
                approve = await client.post(
                    f"/api/v1/workflow/{execution_id}/approve",
                    json={"decision": "approve", "reason": "smoke run"},
                )
                print(f"HTTP {approve.status_code}")
                if approve.status_code != 200:
                    print(json.dumps(approve.json(), ensure_ascii=False, indent=2))
                    return 1

                print("== 3) poll → COMPLETED ==")
                final = await _poll(client, execution_id, "COMPLETED")
                if final.get("status") != "COMPLETED":
                    failed_step = next(
                        (sr for sr in final.get("step_results", []) if sr.get("status") == "FAILED"),
                        {},
                    )
                    print(
                        f"[smoke] FAIL: 终态={final.get('status')}，失败步："
                        f"{failed_step.get('step_id')} {failed_step.get('error')}"
                    )
                    return 1

                forgeloop_out = (final.get("context") or {}).get("forgeloop") or {}
                if not forgeloop_out.get("run_id"):
                    print(f"[smoke] FAIL: 放行后 forgeloop 无 run_id：{forgeloop_out}")
                    return 1

                summary = {
                    "execution_id": execution_id,
                    "status": final["status"],
                    "run_id": forgeloop_out.get("run_id"),
                    "steps": [
                        {"id": sr.get("step_id"), "status": sr.get("status")}
                        for sr in final.get("step_results", [])
                    ],
                }
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                print("[smoke] OK: 闸门前零执行、放行后真跑通（run_id 在场）")
                return 0
        finally:
            loader.clear_loader_cache()
            testkit.reset_workflow_routes_for_tests()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
