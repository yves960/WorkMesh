"""Internal loop-run endpoints. Mounted on the main FastAPI app only.

These are deliberately NOT part of the curated openwebui/openapi.json spec:
Open WebUI agents never see them. They exist for local operators and the
ForgeLoop callback.

Task 3: POST /api/v1/loop/run is DISABLED (410 Gone). Loop runs must go
through the confirmation flow: POST /api/v1/execution/plan -> human review
-> POST /api/v1/execution/confirm. Skipping confirmation is forbidden.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .http_errors import CONTROL_ERRORS, raise_http
from .loops import (
    UnknownLoopRun,
    active_loops,
    record_callback,
)
from .models import LoopCallbackRequest, LoopRunRequest

router = APIRouter()


@router.post("/api/v1/loop/run", operation_id="internal_loop_run")
def loop_run(payload: LoopRunRequest) -> dict[str, Any]:
    """DISABLED: unconfirmed direct runs are forbidden. Returns 410 Gone.

    Use POST /api/v1/execution/plan + POST /api/v1/execution/confirm instead.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "/api/v1/loop/run is gone: loop runs require explicit user"
            " confirmation — create a plan via /api/v1/execution/plan and"
            " confirm it via /api/v1/execution/confirm"
        ),
    )


@router.post("/api/v1/loop/callback", operation_id="internal_loop_callback")
def loop_callback(payload: LoopCallbackRequest) -> dict[str, Any]:
    """Record a ForgeLoop completion callback (memory dict + SQLite history)."""
    try:
        return record_callback(payload)
    except UnknownLoopRun as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@router.get("/loop/status", operation_id="internal_loop_status")
def loop_status() -> dict[str, Any]:
    """Snapshot of every tracked loop run."""
    return {"active_loops": active_loops(), "count": len(active_loops())}
