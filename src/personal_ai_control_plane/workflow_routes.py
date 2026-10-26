"""Workflow Runner endpoints (Phase 1 MVP) — mounted on the MAIN app only.

These routes are intentionally NOT part of the curated openwebui/openapi.json
spec (same policy as loop_routes): Open WebUI sees only the thin bridge
surface; workflow orchestration is control-plane internal.

Endpoints:
    POST /api/v1/workflow/start              idempotent start + background task
    GET  /api/v1/workflow/{execution_id}     instance status + step results
    POST /api/v1/workflow/{execution_id}/approve   record approve/reject decision
    POST /api/v1/workflow/{execution_id}/cancel    cancel a live instance
    GET  /api/v1/workflows                   list workflow definitions
    GET  /api/v1/workflows/{name}            one full definition + raw YAML

Approval gate (2026-10-26 P0): the runner pauses at approval /
requires_approval steps and flips the instance to WAITING_FOR_APPROVAL
(a real enum member since this fix — previously a ghost string).
/approve records the human decision, marks the approval step DONE and
resumes execution; reject transitions to REJECTED and skips the rest.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .workflow import loader, steps  # noqa: F401  (wires loader<->registry)
from .workflow.idempotency import check_and_register, make_idempotency_key
from .workflow.loader import (
    WorkflowLoadError,
    list_workflows,
    load_workflow,
    matches_trigger,
    triggers_summary,
    workflow_path,
)
from .workflow.runner import (
    create_instance,
    load_instance,
    new_execution_id,
    persist_instance,
    record_approval,
    run_workflow,
    skip_pending_steps,
)
from .workflow.state import WorkflowInstance, WorkflowStatus, transition

log = logging.getLogger(__name__)

router = APIRouter()

#: In-memory live instances (execution_id -> instance). Survives restarts via
#: SQLite; anything not here is loaded lazily from the DB.
_INSTANCES: dict[str, WorkflowInstance] = {}
_TASKS: dict[str, asyncio.Task[WorkflowInstance]] = {}


class WorkflowStartRequest(BaseModel):
    workflow_name: str
    trigger_context: dict[str, Any] = {}


class WorkflowApproveRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = None


class WorkflowCancelRequest(BaseModel):
    reason: str | None = None


def reset_workflow_routes_for_tests() -> None:
    _INSTANCES.clear()
    for task in _TASKS.values():
        task.cancel()
    _TASKS.clear()


def _get_instance(execution_id: str) -> WorkflowInstance:
    instance = _INSTANCES.get(execution_id)
    if instance is not None:
        return instance
    loaded = load_instance(execution_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"workflow run not found: {execution_id}")
    return loaded


def _spawn(instance: WorkflowInstance) -> None:
    def _log_failure(task: asyncio.Task[WorkflowInstance]) -> None:
        if not task.cancelled() and task.exception() is not None:
            log.error("workflow %s crashed: %r", instance.execution_id, task.exception())

    task = asyncio.get_running_loop().create_task(run_workflow(instance))
    task.add_done_callback(_log_failure)
    _TASKS[instance.execution_id] = task


#: /approve 只对停在审批闸门的实例有意义（2026-10-26 起 WAITING_FOR_APPROVAL 是真实枚举）。
_LIVE_FOR_APPROVE = (WorkflowStatus.WAITING_FOR_APPROVAL,)
_LIVE_FOR_CANCEL = (WorkflowStatus.PENDING, WorkflowStatus.RUNNING, WorkflowStatus.WAITING_FOR_APPROVAL)


@router.post("/api/v1/workflow/start", operation_id="workflow_start")
async def workflow_start(payload: WorkflowStartRequest) -> dict[str, Any]:
    try:
        defn = load_workflow(payload.workflow_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowLoadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not matches_trigger(defn, payload.trigger_context):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "trigger_context does not match any declared trigger",
                "triggers_summary": triggers_summary(defn),
            },
        )

    key = make_idempotency_key(payload.workflow_name, payload.trigger_context)
    execution_id = new_execution_id()
    existing = await check_and_register(key, execution_id)
    if existing is not None and existing != execution_id:
        # Duplicate trigger: return the existing run, never restart.
        instance = _INSTANCES.get(existing) or load_instance(existing)
        if instance is not None:
            data = instance.to_dict()
            data["idempotent_reuse"] = True
            return data
        # registered but row vanished: fall through and register fresh

    instance = create_instance(payload.workflow_name, payload.trigger_context, execution_id)
    _INSTANCES[instance.execution_id] = instance
    transition(instance, WorkflowStatus.RUNNING)
    persist_instance(instance)
    _spawn(instance)
    return instance.to_dict()


@router.get("/api/v1/workflow/{execution_id}", operation_id="workflow_get_instance")
async def workflow_get(execution_id: str) -> dict[str, Any]:
    return _get_instance(execution_id).to_dict()


@router.post(
    "/api/v1/workflow/{execution_id}/approve", operation_id="workflow_approve"
)
async def workflow_approve(execution_id: str, payload: WorkflowApproveRequest) -> dict[str, Any]:
    instance = _get_instance(execution_id)
    if instance.status is not WorkflowStatus.WAITING_FOR_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot approve: instance status is {instance.status.value};"
                " approve only applies to a WAITING_FOR_APPROVAL instance"
                " (paused at its approval / requires_approval gate)"
            ),
        )
    resume = record_approval(instance, payload.decision, payload.reason)
    if resume:
        _spawn(instance)  # resume：runner 跳过已 DONE 的步，从闸门后继续
    return instance.to_dict()


@router.post("/api/v1/workflow/{execution_id}/cancel", operation_id="workflow_cancel")
async def workflow_cancel(execution_id: str, payload: WorkflowCancelRequest) -> dict[str, Any]:
    instance = _get_instance(execution_id)
    if instance.status not in _LIVE_FOR_CANCEL:
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot cancel: instance status is {instance.status.value};"
                " cancellable statuses: PENDING / RUNNING / WAITING_FOR_APPROVAL"
            ),
        )
    instance.context["cancel_reason"] = payload.reason
    was_waiting = instance.status is WorkflowStatus.WAITING_FOR_APPROVAL
    transition(instance, WorkflowStatus.CANCELLED)
    persist_instance(instance)
    # RUNNING 态由 runner 在下一个步边界注意到并 SKIPPED 余步；
    # WAITING 态 runner 任务已停，路由侧直接补刀。
    if was_waiting:
        skip_pending_steps(instance)
    return instance.to_dict()


@router.get("/api/v1/workflows", operation_id="workflow_list_definitions")
async def workflow_list() -> dict[str, Any]:
    return {
        "workflows": [
            {
                "name": defn.name,
                "description": defn.description,
                "triggers_summary": triggers_summary(defn),
            }
            for defn in list_workflows()
        ]
    }


@router.get("/api/v1/workflows/{name}", operation_id="workflow_get_definition")
async def workflow_get_definition(name: str) -> dict[str, Any]:
    try:
        defn = load_workflow(name)
        raw = workflow_path(name).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowLoadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = defn.model_dump()
    data["yaml"] = raw
    return data
