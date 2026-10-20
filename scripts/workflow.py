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


def cmd_list(_args: argparse.Namespace) -> int:
    return _request("GET", "/api/v1/workflows")


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
    return _request(
        "POST",
        "/api/v1/workflow/start",
        {"workflow_name": args.name, "trigger_context": trigger},
    )


def cmd_status(args: argparse.Namespace) -> int:
    return _request("GET", f"/api/v1/workflow/{args.execution_id}")


def cmd_approve(args: argparse.Namespace) -> int:
    return _request(
        "POST",
        f"/api/v1/workflow/{args.execution_id}/approve",
        {"decision": args.decision, "reason": args.reason},
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
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow", description="WorkMesh Workflow CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="列出全部工作流定义")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="查看单个工作流定义")
    p.add_argument("name")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("start", help="启动工作流（幂等：同 trigger 重放返回原 execution_id）")
    p.add_argument("name")
    p.add_argument("--set", action="append", metavar="k=v", help="trigger_context 字段，可重复")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("status", help="查询执行实例")
    p.add_argument("execution_id")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("approve", help="审批（approve/reject）")
    p.add_argument("execution_id")
    p.add_argument("--decision", choices=["approve", "reject"], default="approve")
    p.add_argument("--reason", default=None)
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("lint", help="本地校验 YAML（含前向引用静态检查），无需服务")
    p.add_argument("name")
    p.set_defaults(func=cmd_lint)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
