#!/usr/bin/env python3
"""Workflow CLI — 30 秒上手：list / show / start / status / approve / lint。

默认走 HTTP（AICP_API_BASE，默认 http://127.0.0.1:8765，trust_env=False 不读代理）；
lint 子命令本地加载 YAML 校验（含前向引用静态检查），不依赖服务。

用法示例：
  python3 scripts/workflow.py lint java-ut-repair
  python3 scripts/workflow.py list
  python3 scripts/workflow.py start java-ut-repair --set user_message="UT 挂了" --set project_path=/tmp/demo
  python3 scripts/workflow.py status <execution_id>
  python3 scripts/workflow.py approve <execution_id> --reason "看起来没问题"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx  # noqa: E402

BASE = os.environ.get("AICP_API_BASE", "http://127.0.0.1:8765").rstrip("/")


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=30.0, trust_env=False)


def _print(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _request(method: str, path: str, json_body: dict | None = None) -> int:
    try:
        with _client() as client:
            resp = client.request(method, path, json=json_body)
    except httpx.HTTPError as exc:
        print(f"[workflow] ERROR: 无法连接 {BASE}（{exc}）；服务起了吗？")
        return 1
    body = resp.json() if resp.content else {}
    _print(body)
    if resp.status_code >= 400:
        print(f"[workflow] HTTP {resp.status_code}", file=sys.stderr)
        return 1
    return 0


def _fetch(method: str, path: str, json_body: dict | None = None) -> tuple[dict | None, int]:
    """摘要模式专用：返回 (body, exit_code)——非 2xx/连接失败自带 stderr 输出。"""
    try:
        with _client() as client:
            resp = client.request(method, path, json=json_body)
    except httpx.HTTPError as exc:
        print(f"[workflow] ERROR: 无法连接 {BASE}（{exc}）；服务起了吗？")
        return None, 1
    if resp.status_code >= 400:
        print(f"[workflow] HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return None, 1
    return (resp.json() if resp.content else {}), 0


#: 蔚 2026-11-02 P2-5：失败步「怎么查」话术（按 error_code 分流）
HOWTO = {
    "UPSTREAM_HTTP": "上游接口报错——看「原因」里的 detail；目标服务起没起、路径对不对",
    "TIMEOUT": "该步超时——查工作流里该步的 timeout_seconds 与上游响应时长",
    "STEP_LOGIC": "业务前置条件不满足——看「原因」里的说明，修触发上下文或数据",
}


def _humanize_detail(raw: str | None) -> str:
    """error_detail 可能是上游裸 JSON——抠出 detail/message 字段给人读
    （蔚 11-02 实测样本：loop executable not found 的提示就藏在 detail 里）。"""
    text = raw or ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            text = str(parsed.get("detail") or parsed.get("message") or text)
    except (ValueError, TypeError):
        pass
    return text[:200]


def _render_status_summary(body: dict) -> None:
    """蔚 2026-11-02 P3-2：默认摘要——一行总览 + 非 PENDING 步骤；--json 才全量。"""
    steps = body.get("step_results") or []
    non_pending = [s for s in steps if s.get("status") != "PENDING"]
    marks = {"DONE": "✓", "FAILED": "✗", "SKIPPED": "－", "RUNNING": "▶"}
    print(
        f"{body.get('execution_id')} · {body.get('status')} ·"
        f" 当前步 {body.get('current_step') or '-'} · 已推进 {len(non_pending)}/{len(steps)} 步"
    )
    for s in non_pending:
        line = f"  {marks.get(s.get('status'), '?')} {str(s.get('step_id')):<14} {s.get('status')}"
        if s.get("duration_ms") is not None:
            line += f" · {s['duration_ms']}ms"
        print(line)
        if s.get("status") == "FAILED":
            code = s.get("error_code") or "?"
            status = s.get("http_status")
            suffix = f" {status}" if status else ""
            print(f"      └ 原因 [{code}{suffix}]: {_humanize_detail(s.get('error_detail') or s.get('error'))}")
            print(f"      └ 怎么查: {HOWTO.get(code, '看 error/error_detail 与上游服务日志')}")
    if body.get("status") == "WAITING_FOR_APPROVAL":
        print("⏸ 等待审批：workflow.py approve <execution_id> --decision approve --reason '...'")


def cmd_list(args: argparse.Namespace) -> int:
    if args.json:
        return _request("GET", "/api/v1/workflows")
    body, code = _fetch("GET", "/api/v1/workflows")
    if code == 0:
        for wf in body.get("workflows", []):
            latest = wf.get("latest_run")
            if latest:
                print(
                    f"{str(wf.get('name', '?')):<18}"
                    f" 最近: {latest.get('execution_id')} · {latest.get('status')} · {latest.get('updated_at')}"
                )
            else:
                print(f"{str(wf.get('name', '?')):<18} 最近: 无执行记录")
    return code


def cmd_show(args: argparse.Namespace) -> int:
    return _request("GET", f"/api/v1/workflows/{args.name}")


def cmd_start(args: argparse.Namespace) -> int:
    trigger: dict[str, str] = {}
    for item in args.set or []:
        if "=" not in item:
            print(f"[workflow] ERROR: --set 需要 k=v 形式，收到 {item!r}")
            return 2
        key, value = item.split("=", 1)
        trigger[key] = value
    payload = {"workflow_name": args.name, "trigger_context": trigger}
    if args.json:
        return _request("POST", "/api/v1/workflow/start", payload)
    body, code = _fetch("POST", "/api/v1/workflow/start", payload)
    if code == 0:
        print(f"{body.get('execution_id')} · {body.get('status')}")
        if body.get("idempotent_reuse"):
            print("  （幂等复用：同 trigger 已有实例，未重复启动）")
        if body.get("warning"):
            print(f"  ⚠ {body['warning']}")
        print("  下一步：workflow.py status <execution_id>；到审批闸门时 approve <id> --decision approve")
    return code


def cmd_status(args: argparse.Namespace) -> int:
    if args.json:
        return _request("GET", f"/api/v1/workflow/{args.execution_id}")
    body, code = _fetch("GET", f"/api/v1/workflow/{args.execution_id}")
    if code == 0:
        _render_status_summary(body)
    return code


def cmd_approve(args: argparse.Namespace) -> int:
    return _request(
        "POST",
        f"/api/v1/workflow/{args.execution_id}/approve",
        {"decision": args.decision, "reason": args.reason},
    )


def cmd_cancel(args: argparse.Namespace) -> int:
    return _request(
        "POST",
        f"/api/v1/workflow/{args.execution_id}/cancel",
        {"reason": args.reason},
    )


def cmd_lint(args: argparse.Namespace) -> int:
    from personal_ai_control_plane.workflow.loader import (
        WorkflowLoadError,
        load_workflow,
    )

    try:
        defn = load_workflow(args.name)
    except (WorkflowLoadError, FileNotFoundError) as exc:
        print(f"[lint] ERROR: {exc}")
        return 1
    print(f"[lint] OK: {defn.name} v{defn.version} — {len(defn.steps)} steps, {len(defn.triggers)} triggers")
    for step in defn.steps:
        marker = " (requires_approval)" if step.requires_approval else ""
        print(f"  - {step.id} [{step.type}]{marker}")
    # 蔚 2026-11-02 P3-4：preflight（与 start 端同口径）——审批注意力别浪费在必败局上
    import shutil
    if any(step.type == "forgeloop" for step in defn.steps):
        executable = os.environ.get("AICP_LOOP_EXECUTABLE", "loop")
        if shutil.which(executable) is None:
            print(
                f"[lint] ⚠ preflight: forgeloop 需要 '{executable}' 可执行，"
                "PATH 未找到且 AICP_LOOP_EXECUTABLE 未设——运行到该步必失败"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow", description="WorkMesh Workflow CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="列出全部工作流定义 + 最近执行摘要")
    p.add_argument("--json", action="store_true", help="全量 JSON 输出")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="查看单个工作流定义")
    p.add_argument("name")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("start", help="启动工作流（幂等：同 trigger 重放返回原 execution_id）")
    p.add_argument("name")
    p.add_argument("--set", action="append", metavar="k=v", help="trigger_context 字段，可重复")
    p.add_argument("--json", action="store_true", help="全量 JSON 输出")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("status", help="查询执行实例（默认摘要：一行总览 + 非 PENDING 步骤）")
    p.add_argument("execution_id")
    p.add_argument("--json", action="store_true", help="全量 JSON（含 inputs/outputs/PENDING 骨架）")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("approve", help="审批（approve/reject，仅 WAITING_FOR_APPROVAL 态）")
    p.add_argument("execution_id")
    p.add_argument("--decision", choices=["approve", "reject"], default="approve")
    p.add_argument("--reason", default=None)
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("cancel", help="取消 live 实例（PENDING/RUNNING/WAITING_FOR_APPROVAL）")
    p.add_argument("execution_id")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("lint", help="本地校验 YAML（含前向引用静态检查），无需服务")
    p.add_argument("name")
    p.set_defaults(func=cmd_lint)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
