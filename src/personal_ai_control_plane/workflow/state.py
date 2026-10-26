"""Workflow instance state machine + dataclasses (Phase 1).

Statuses:
    Workflow: PENDING -> RUNNING -> WAITING_FOR_APPROVAL -> RUNNING -> COMPLETED
             (WAITING_FOR_APPROVAL 也可 -> REJECTED / CANCELLED)
    Step:     PENDING -> RUNNING -> DONE | FAILED | SKIPPED

WAITING_FOR_APPROVAL（2026-10-26 P0-2 补齐）：runner 在 approval / requires_approval
步前停下并转入此状态；POST /approve 才回到 RUNNING 继续。此前它只以裸字符串
存在于路由文案里（幽灵状态），现归位为真枚举。

Timestamps are float epoch seconds internally (cheap duration math) and are
serialized to ISO-8601 UTC strings in to_dict() for the API surface.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class WorkflowStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


#: Terminal statuses: no further transitions are legal.
_TERMINAL = {
    WorkflowStatus.COMPLETED,
    WorkflowStatus.FAILED,
    WorkflowStatus.REJECTED,
    WorkflowStatus.CANCELLED,
}

_VALID_TRANSITIONS: dict[WorkflowStatus, set[WorkflowStatus]] = {
    WorkflowStatus.PENDING: {WorkflowStatus.RUNNING, WorkflowStatus.CANCELLED},
    WorkflowStatus.RUNNING: {
        WorkflowStatus.WAITING_FOR_APPROVAL,
        WorkflowStatus.COMPLETED,
        WorkflowStatus.FAILED,
        WorkflowStatus.REJECTED,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.WAITING_FOR_APPROVAL: {
        WorkflowStatus.RUNNING,
        WorkflowStatus.REJECTED,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.COMPLETED: set(),
    WorkflowStatus.FAILED: set(),
    WorkflowStatus.REJECTED: set(),
    WorkflowStatus.CANCELLED: set(),
}


class InvalidTransitionError(Exception):
    """Raised when a status change violates the state machine."""

    def __init__(self, current: WorkflowStatus, new: WorkflowStatus):
        self.current = current
        self.new = new
        super().__init__(f"illegal transition {current.value} -> {new.value}")


def _iso(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


@dataclass
class StepResult:
    step_id: str
    step_index: int
    status: StepStatus = StepStatus.PENDING
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    error: str | None = None
    started_at: float | None = None
    updated_at: float | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_index": self.step_index,
            "status": self.status.value,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "error": self.error,
            "started_at": _iso(self.started_at),
            "updated_at": _iso(self.updated_at),
            "duration_ms": self.duration_ms,
        }


@dataclass
class WorkflowInstance:
    execution_id: str
    workflow_name: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    trigger_context: dict[str, Any] = field(default_factory=dict)
    #: {"trigger": {...}, "<step_id>": {outputs...}, "workflow": {...}}
    context: dict[str, Any] = field(default_factory=dict)
    step_results: list[StepResult] = field(default_factory=list)
    started_at: float | None = None
    updated_at: float | None = None
    completed_at: float | None = None

    @property
    def current_step(self) -> str | None:
        """Id of the first step that is not yet DONE/SKIPPED (None = finished)."""
        for sr in self.step_results:
            if sr.status not in (StepStatus.DONE, StepStatus.SKIPPED):
                return sr.step_id
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "workflow_name": self.workflow_name,
            "status": self.status.value,
            "current_step": self.current_step,
            "trigger_context": self.trigger_context,
            "context": self.context,
            "step_results": [sr.to_dict() for sr in self.step_results],
            "started_at": _iso(self.started_at),
            "updated_at": _iso(self.updated_at),
            "completed_at": _iso(self.completed_at),
        }


def transition(instance: WorkflowInstance, new_status: WorkflowStatus) -> None:
    """Validate + apply a WorkflowStatus change; raises InvalidTransitionError."""
    if not isinstance(new_status, WorkflowStatus):
        new_status = WorkflowStatus(new_status)
    allowed = _VALID_TRANSITIONS[instance.status]
    if new_status not in allowed:
        raise InvalidTransitionError(instance.status, new_status)
    now = time.time()
    if new_status == WorkflowStatus.RUNNING and instance.started_at is None:
        instance.started_at = now
    instance.status = new_status
    instance.updated_at = now
    if new_status in _TERMINAL:
        instance.completed_at = now
