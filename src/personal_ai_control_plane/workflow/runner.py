"""Workflow runner: sequential step loop with per-step timeout + immediate
SQLite persistence on every state change.

Phase 1: strictly sequential (no parallel steps), no retry. A FAILED
non-optional step fails the whole instance and SKIPS the rest. External
approve/cancel flip instance.status between steps; the loop notices and
marks remaining steps SKIPPED.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from ..db import connect, init_db
from .loader import load_workflow
from .state import (
    StepResult,
    StepStatus,
    WorkflowInstance,
    WorkflowStatus,
    transition,
)
from .steps import STEP_REGISTRY, StepContext, render_inputs

#: Fallback timeout for steps that do not declare one (spec: timeouts enforced).
DEFAULT_STEP_TIMEOUT_SECONDS = 120

#: Statuses an external actor (approve/cancel) may have moved us to.
_HALTED_STATUSES = (WorkflowStatus.CANCELLED, WorkflowStatus.REJECTED)


def new_execution_id() -> str:
    """wf-YYYYMMDD-XXXXXX (UTC date + 6 lowercase hex chars, Task 1 style)."""
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"wf-{stamp}-{secrets.token_hex(3)}"


# ---------------------------------------------------------------------------
# Persistence (immediate, per change — never batched)
# ---------------------------------------------------------------------------

def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def persist_instance(instance: WorkflowInstance) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO workflow_runs (execution_id, workflow_name, status,"
            " trigger_source, trigger_context, context, started_at, updated_at, completed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(execution_id) DO UPDATE SET"
            " status = excluded.status, trigger_context = excluded.trigger_context,"
            " context = excluded.context, updated_at = excluded.updated_at,"
            " completed_at = excluded.completed_at",
            (
                instance.execution_id,
                instance.workflow_name,
                instance.status.value,
                str((instance.trigger_context or {}).get("source") or ""),
                _dump(instance.trigger_context),
                _dump(instance.context),
                _iso_or_none(instance.started_at),
                _iso_or_none(instance.updated_at),
                _iso_or_none(instance.completed_at),
            ),
        )


def persist_step_result(execution_id: str, step: StepResult) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO workflow_step_results (execution_id, step_id, step_index,"
            " status, inputs, outputs, error, started_at, updated_at, duration_ms)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(execution_id, step_id) DO UPDATE SET"
            " status = excluded.status, inputs = excluded.inputs,"
            " outputs = excluded.outputs, error = excluded.error,"
            " started_at = excluded.started_at, updated_at = excluded.updated_at,"
            " duration_ms = excluded.duration_ms",
            (
                execution_id,
                step.step_id,
                step.step_index,
                step.status.value,
                _dump(step.inputs) if step.inputs is not None else None,
                _dump(step.outputs) if step.outputs is not None else None,
                step.error,
                _iso_or_none(step.started_at),
                _iso_or_none(step.updated_at),
                step.duration_ms,
            ),
        )


def _iso_or_none(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


def _epoch_or_none(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def load_instance(execution_id: str) -> WorkflowInstance | None:
    """Rebuild a WorkflowInstance from SQLite (after restart / other process)."""
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        if row is None:
            return None
        steps = conn.execute(
            "SELECT * FROM workflow_step_results WHERE execution_id = ?"
            " ORDER BY step_index",
            (execution_id,),
        ).fetchall()
    instance = WorkflowInstance(
        execution_id=row["execution_id"],
        workflow_name=row["workflow_name"],
        status=WorkflowStatus(row["status"]),
        trigger_context=json.loads(row["trigger_context"] or "{}"),
        context=json.loads(row["context"] or "{}"),
        step_results=[
            StepResult(
                step_id=s["step_id"],
                step_index=s["step_index"],
                status=StepStatus(s["status"]),
                inputs=json.loads(s["inputs"]) if s["inputs"] else None,
                outputs=json.loads(s["outputs"]) if s["outputs"] else None,
                error=s["error"],
                started_at=_epoch_or_none(s["started_at"]),
                updated_at=_epoch_or_none(s["updated_at"]),
                duration_ms=s["duration_ms"],
            )
            for s in steps
        ],
        started_at=_epoch_or_none(row["started_at"]),
        updated_at=_epoch_or_none(row["updated_at"]),
        completed_at=_epoch_or_none(row["completed_at"]),
    )
    return instance


def create_instance(
    workflow_name: str, trigger_context: dict[str, Any], execution_id: str | None = None
) -> WorkflowInstance:
    """Build a PENDING instance (all steps PENDING) and persist it."""
    defn = load_workflow(workflow_name)
    execution_id = execution_id or new_execution_id()
    now = time.time()
    instance = WorkflowInstance(
        execution_id=execution_id,
        workflow_name=workflow_name,
        status=WorkflowStatus.PENDING,
        trigger_context=dict(trigger_context or {}),
        context={
            "trigger": dict(trigger_context or {}),
            "workflow": {"name": defn.name, "version": defn.version},
        },
        step_results=[
            StepResult(step_id=step.id, step_index=i) for i, step in enumerate(defn.steps)
        ],
        started_at=now,
        updated_at=now,
    )
    persist_instance(instance)
    for step in instance.step_results:
        persist_step_result(execution_id, step)
    return instance


# ---------------------------------------------------------------------------
# The main loop
# ---------------------------------------------------------------------------

def _mark(instance: WorkflowInstance, step: StepResult, status: StepStatus) -> None:
    step.status = status
    step.updated_at = time.time()
    instance.updated_at = step.updated_at
    persist_step_result(instance.execution_id, step)
    persist_instance(instance)


def _predecessors_ok(instance: WorkflowInstance, defn, index: int) -> bool:
    """May step[index] run? Every predecessor must be DONE (an optional
    predecessor that FAILED is treated as transparently skipped)."""
    for i in range(index):
        sr = instance.step_results[i]
        if sr.status is StepStatus.DONE:
            continue
        if sr.status is StepStatus.FAILED and defn.steps[i].optional:
            continue
        return False
    return True


async def run_workflow(instance: WorkflowInstance) -> WorkflowInstance:
    """Execute every step in order. Requires status == RUNNING.

    审批闸门（2026-10-26 P0-1）：
    - type == "approval" 的步在未获人工批准时把实例转入 WAITING_FOR_APPROVAL 并停住
      （步本身保持 PENDING，等 POST /approve 记录决策后置 DONE 并重新拉起本函数）；
    - requires_approval: true 的步执行前强制校验 context["approval"].decision == "approve"
      （双保险：即使 YAML 里漏了 approval 步，危险动作也不会自动跑）。
    重入安全：已 DONE/SKIPPED 的步直接跳过（approve 后 resume 复用本循环）。
    """
    if instance.status != WorkflowStatus.RUNNING:
        raise ValueError(
            f"run_workflow requires RUNNING instance, got {instance.status.value}"
        )
    defn = load_workflow(instance.workflow_name)

    for index, step_def in enumerate(defn.steps):
        result = instance.step_results[index]

        # resume 跳过：已完结的步不重跑（approve 后二次拉起时走到这里）
        if result.status in (StepStatus.DONE, StepStatus.SKIPPED):
            continue

        # External approve/cancel moved us out of RUNNING: stop, skip the rest.
        if instance.status in _HALTED_STATUSES:
            if result.status == StepStatus.PENDING:
                _mark(instance, result, StepStatus.SKIPPED)
            continue

        # A prior non-optional step not DONE (FAILED/SKIPPED) => SKIPPED.
        if not _predecessors_ok(instance, defn, index):
            if result.status == StepStatus.PENDING:
                _mark(instance, result, StepStatus.SKIPPED)
            continue

        # 审批闸门 1：approval 步未获人工批准 → 停在等待态（不执行 stub）
        approved = (instance.context.get("approval") or {}).get("decision") == "approve"
        if step_def.type == "approval" and not approved:
            transition(instance, WorkflowStatus.WAITING_FOR_APPROVAL)
            persist_instance(instance)
            return instance

        # 审批闸门 2（双保险，WORKFLOW_DESIGN.md:88）：危险动作步前必须有人批过
        if step_def.requires_approval and not approved:
            transition(instance, WorkflowStatus.WAITING_FOR_APPROVAL)
            persist_instance(instance)
            return instance

        _mark(instance, result, StepStatus.RUNNING)
        result.started_at = time.time()
        persist_step_result(instance.execution_id, result)

        rendered = render_inputs(step_def.inputs, instance.context)
        result.inputs = rendered
        factory = STEP_REGISTRY[step_def.type]

        timeout = step_def.timeout_seconds or DEFAULT_STEP_TIMEOUT_SECONDS
        outputs: dict[str, Any] | None = None
        try:
            outputs = await asyncio.wait_for(
                factory(
                    StepContext(
                        workflow_name=instance.workflow_name,
                        step_id=step_def.id,
                        inputs=rendered,
                        context=instance.context,
                    )
                ),
                timeout=timeout,
            )
        except TimeoutError:
            result.error = f"step timed out after {timeout}s"
            result.duration_ms = int((time.time() - result.started_at) * 1000)
            _mark(instance, result, StepStatus.FAILED)
        except Exception as exc:  # noqa: BLE001 - one step failure must not kill the loop
            result.error = f"{type(exc).__name__}: {exc}"
            result.duration_ms = int((time.time() - result.started_at) * 1000)
            _mark(instance, result, StepStatus.FAILED)

        if result.status == StepStatus.FAILED:
            if step_def.optional:
                continue  # optional failure: keep going
            transition(instance, WorkflowStatus.FAILED)
            persist_instance(instance)
            for rest in instance.step_results[index + 1 :]:
                if rest.status == StepStatus.PENDING:
                    _mark(instance, rest, StepStatus.SKIPPED)
            return instance

        assert outputs is not None  # only reachable on success
        result.outputs = outputs
        result.duration_ms = int((time.time() - result.started_at) * 1000)
        _mark(instance, result, StepStatus.DONE)
        instance.context[step_def.id] = outputs
        persist_instance(instance)

    if instance.status == WorkflowStatus.RUNNING:
        transition(instance, WorkflowStatus.COMPLETED)
        persist_instance(instance)
    return instance


# ---------------------------------------------------------------------------
# 审批闸门 API（routes 与 tests 共用，单一来源）
# ---------------------------------------------------------------------------

def skip_pending_steps(instance: WorkflowInstance) -> None:
    """剩余 PENDING 步置 SKIPPED（WAITING 被 reject/cancel 时 runner 任务已停，调用方补刀）。"""
    for sr in instance.step_results:
        if sr.status is StepStatus.PENDING:
            sr.status = StepStatus.SKIPPED
            sr.updated_at = time.time()
            persist_step_result(instance.execution_id, sr)
    instance.updated_at = time.time()
    persist_instance(instance)


def record_approval(instance: WorkflowInstance, decision: str, reason: str | None) -> bool:
    """记录人工审批决策（调用方保证实例处于 WAITING_FOR_APPROVAL）。

    approve：写 context + 渲染并记录 approval 步的 inputs（计划留痕供审阅）+ 置 DONE，
             WAITING_FOR_APPROVAL → RUNNING，返回 True（调用方应重新拉起 run_workflow 续跑）；
    reject： 写 context → REJECTED + 余步置 SKIPPED，返回 False。
    """
    now_iso = datetime.now(UTC).isoformat()
    instance.context["approval"] = {
        "decision": decision,
        "reason": reason,
        "decided_at": now_iso,
    }
    if decision == "reject":
        transition(instance, WorkflowStatus.REJECTED)
        skip_pending_steps(instance)
        return False

    defn = load_workflow(instance.workflow_name)
    for sr in instance.step_results:
        if sr.status is StepStatus.PENDING and defn.steps[sr.step_index].type == "approval":
            sr.inputs = render_inputs(defn.steps[sr.step_index].inputs, instance.context)
            sr.status = StepStatus.DONE
            sr.outputs = {"decision": "approve", "reason": reason, "approved_at": now_iso}
            sr.updated_at = time.time()
            persist_step_result(instance.execution_id, sr)
            break
    transition(instance, WorkflowStatus.RUNNING)
    persist_instance(instance)
    return True
