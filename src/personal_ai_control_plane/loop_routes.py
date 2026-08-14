"""Internal loop-run endpoints. Mounted on the main FastAPI app only.

These are deliberately NOT part of the curated openwebui/openapi.json spec:
Open WebUI agents never see them. They exist for local operators and the
ForgeLoop callback.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .http_errors import CONTROL_ERRORS, raise_http
from .loops import UnknownLoopRun, active_loops, record_callback, start_loop
from .models import LoopCallbackRequest, LoopRunRequest

router = APIRouter()


@router.post("/api/v1/loop/run", operation_id="internal_loop_run")
def loop_run(payload: LoopRunRequest) -> dict[str, Any]:
    """Start `loop run` as a detached subprocess. Returns run_id immediately."""
    try:
        return start_loop(payload)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


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
