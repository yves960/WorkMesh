"""Step registry: 8 Phase-1 factories (2 real HTTP, 6 stubs).

Real steps call this control plane's own Task 2/3 endpoints over HTTP so the
workflow exercises exactly the public contract Open WebUI would:
    discovery_step  -> POST /api/v1/discovery
    forgeloop_step  -> POST /api/v1/execution/plan (if no plan_id upstream)
                        + POST /api/v1/execution/confirm

Stubs return {"stub": true, ...} so the runner can complete end-to-end and
Phase 2 swaps them for real implementations without touching the runner.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

StepFactory = Callable[["StepContext"], Awaitable[dict[str, Any]]]


class StepError(Exception):
    """A step failed (non-2xx upstream, transport error, bad payload)."""


@dataclass
class StepContext:
    workflow_name: str
    step_id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    #: {"trigger": {...}, "<step_id>": {outputs...}, "workflow": {name, version}}
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def trigger(self) -> dict[str, Any]:
        return dict(self.context.get("trigger") or {})


# ---------------------------------------------------------------------------
# Template rendering: {{path.to.value}} / {{list[0]}} against the context.
# ---------------------------------------------------------------------------

_TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.\[\]-]+?)\s*\}\}")


def _resolve_path(path: str, context: dict[str, Any]) -> tuple[bool, Any]:
    """Resolve 'a.b[0].c' against nested dicts/lists. Returns (found, value)."""
    node: Any = context
    tokens = re.split(r"\.", path)
    for token in tokens:
        index: int | None = None
        m = re.fullmatch(r"([^\[\]]+)\[(\d+)\]", token)
        if m:
            token, index = m.group(1), int(m.group(2))
        if isinstance(node, dict):
            if token not in node:
                return False, None
            node = node[token]
        else:
            return False, None
        if index is not None:
            if not isinstance(node, list) or index >= len(node):
                return False, None
            node = node[index]
    return True, node


def render_value(value: Any, context: dict[str, Any]) -> Any:
    """Render one value. A value that IS a single template returns the raw
    (possibly non-string) referenced value; embedded templates become str()."""
    if isinstance(value, str):
        full = _TEMPLATE_RE.fullmatch(value.strip())
        if full:
            found, resolved = _resolve_path(full.group(1), context)
            return resolved if found else None
        def _sub(match: re.Match[str]) -> str:
            found, resolved = _resolve_path(match.group(1), context)
            return "" if not found or resolved is None else str(resolved)
        return _TEMPLATE_RE.sub(_sub, value)
    if isinstance(value, list):
        return [render_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: render_value(item, context) for key, item in value.items()}
    return value


def render_inputs(raw_inputs: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {key: render_value(value, context) for key, value in raw_inputs.items()}


# ---------------------------------------------------------------------------
# HTTP plumbing for the two real steps
# ---------------------------------------------------------------------------

def api_base_url() -> str:
    return os.getenv("AICP_API_BASE", "http://127.0.0.1:8765").rstrip("/")


def _make_client() -> httpx.AsyncClient:
    """Indirection point: tests swap in an ASGITransport client.

    trust_env=False: 不读 HTTP(S)_PROXY 环境代理——否则走系统代理的开发机
    在 discovery step 会 502（P1 债，43 天，本次随 WIP 入库一并修死）。
    """
    return httpx.AsyncClient(base_url=api_base_url(), timeout=30.0, trust_env=False)


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    async with _make_client() as client:
        resp = await client.post(path, json=payload)
    if resp.status_code >= 400:
        raise StepError(f"POST {path} -> {resp.status_code}: {resp.text[:500]}")
    return resp.json()


# ---------------------------------------------------------------------------
# The 8 factories
# ---------------------------------------------------------------------------

async def knowledge_step(ctx: StepContext) -> dict[str, Any]:
    query = str(ctx.inputs.get("query") or "")
    return {
        "stub": True,
        "would_have": f"query KB: {query}",
        "knowledge_results": [],
    }


async def context_step(ctx: StepContext) -> dict[str, Any]:
    include = ctx.inputs.get("include") or []
    return {
        "stub": True,
        "context_keys": [str(item) for item in include],
    }


async def discovery_step(ctx: StepContext) -> dict[str, Any]:
    payload = {
        "user_message": str(ctx.inputs.get("user_message") or ""),
        "workspace_root": str(ctx.inputs.get("workspace_root") or ""),
    }
    result = await _post("/api/v1/discovery", payload)
    if not result.get("recommended_target"):
        raise StepError(
            f"discovery found no failing test to target: {result.get('failure_summary', '')[:200]}"
        )
    return result


async def issue_step(ctx: StepContext) -> dict[str, Any]:
    return {
        "stub": True,
        "would_have": "create/update issue",
        "issue_id": "stub-issue",
    }


async def approval_step(ctx: StepContext) -> dict[str, Any]:
    # Phase 1: auto-approve. Phase 2 replaces this with a real wait on
    # POST /workflow/{id}/approve (the endpoint already records decisions).
    return {
        "stub": True,
        "auto_approved": True,
        "approved": True,
        "approved_at": datetime.now(UTC).isoformat(),
    }


async def forgeloop_step(ctx: StepContext) -> dict[str, Any]:
    """Plan (if needed) + confirm one ForgeLoop run via the Task 3 endpoints.

    plan_id comes from upstream context when present (Phase 2 approval step
    will create it); otherwise the step creates a plan from the discovery
    output. The phase-1 approval stub auto-approves, so confirming here is
    the explicit user confirmation recorded in the workflow itself.
    """
    discovery = dict(ctx.context.get("discovery") or {})
    overrides: dict[str, Any] = {}
    if ctx.inputs.get("profile"):
        overrides["profile"] = ctx.inputs.get("profile")
    if ctx.inputs.get("target"):
        overrides["target"] = ctx.inputs.get("target")
    if ctx.inputs.get("issue_ref"):
        overrides["issue_ref"] = ctx.inputs.get("issue_ref")

    plan_id = str(ctx.inputs.get("plan_id") or ctx.context.get("plan_id") or "")
    if not plan_id:
        plan = await _post(
            "/api/v1/execution/plan",
            {"discovery": discovery, "user_overrides": overrides or None},
        )
        plan_id = plan["plan_id"]
    confirmed = await _post("/api/v1/execution/confirm", {"plan_id": plan_id})
    return {
        "result": confirmed.get("status", "launched"),
        "execution_id": confirmed.get("execution_id"),
        "plan_id": plan_id,
        "run_id": confirmed.get("run_id"),
    }


async def sync_multica_step(ctx: StepContext) -> dict[str, Any]:
    return {
        "stub": True,
        "would_have": "sync status",
        "multica_status": None,
    }


async def notify_step(ctx: StepContext) -> dict[str, Any]:
    return {
        "stub": True,
        "would_have": "notify user",
    }


#: name -> factory. register_step() may add/replace entries (tests do this).
STEP_REGISTRY: dict[str, StepFactory] = {
    "knowledge": knowledge_step,
    "context": context_step,
    "discovery": discovery_step,
    "issue": issue_step,
    "approval": approval_step,
    "forgeloop": forgeloop_step,
    "sync_multica": sync_multica_step,
    "notify": notify_step,
}


def register_step(name: str, factory: StepFactory) -> None:
    """Register (or replace) a step factory by name."""
    STEP_REGISTRY[name] = factory
