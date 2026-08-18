"""Task 3: Execution Plan + explicit user confirmation flow.

Safety contract (hard rule): a ForgeLoop run may ONLY start after the user
confirms a plan.

    POST /api/v1/discovery            -> DiscoveryResult (Task 2, nothing runs)
    POST /api/v1/execution/plan       -> inert Plan (nothing runs)
    [human reviews target/profile/issue_ref]
    POST /api/v1/execution/confirm    -> consumes the plan one-shot and calls
                                         start_loop() inside loops._GUARD()

The legacy direct-run endpoint POST /api/v1/loop/run is 410 Gone. As defense
in depth, start_loop() itself refuses to run outside loops._GUARD().
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from .db import utcnow
from .http_errors import CONTROL_ERRORS, raise_http
from .loops import _GUARD, start_loop
from .models import LoopRunRequest

router = APIRouter()

#: plan_id -> Plan. Plans are consumed atomically on confirm (one-shot:
#: a plan can never launch twice, and a launched plan is gone forever).
_PLANS: dict[str, Plan] = {}
_PLANS_LOCK = threading.Lock()

#: The only fields a user may override on a discovered plan.
ALLOWED_OVERRIDE_FIELDS = frozenset({"target", "profile", "issue_ref"})

DEFAULT_PROFILE = "java-ut-fixer"


@dataclass
class Plan:
    plan_id: str
    execution_id: str
    project: str
    project_path: str
    repo: str | None
    profile: str
    target: str
    related_issue: str | None
    estimated_minutes: int
    requires_confirmation: bool
    created_at: str
    # Internal, never serialized: the exact run launched on confirmation.
    run_request: LoopRunRequest


class ExecutionPlanRequest(BaseModel):
    discovery: dict[str, Any] = Field(
        default_factory=dict,
        description="DiscoveryResult from POST /api/v1/discovery",
    )
    user_overrides: dict[str, Any] | None = Field(
        default=None,
        description="Optional overrides; ONLY target/profile/issue_ref are allowed",
    )


class ConfirmRequest(BaseModel):
    plan_id: str = Field(min_length=1)


def reset_plans_for_tests() -> None:
    _PLANS.clear()


def _new_plan_id() -> str:
    """plan-YYYYMMDD-XXXXXX (UTC date + 6 lowercase hex chars)."""
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"plan-{stamp}-{secrets.token_hex(3)}"


def _new_execution_id() -> str:
    """exec-YYYYMMDD-XXXXXX (same shape loops.start_loop generates)."""
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"exec-{stamp}-{secrets.token_hex(3)}"


def _estimate_minutes(target: str) -> int:
    """Rough loop ETA from target granularity.

    - 'com.demo.FooTest#testBar' ->  2 min (single test method)
    - 'com.demo.FooTest'         ->  5 min (single class; UpperCamel last
                                      segment, Java class convention)
    - 'com.demo' / 'com.demo.*'  -> 10 min (package path or wildcard)
    """
    if "#" in target:
        return 2
    last_segment = target.strip().rsplit(".", 1)[-1].strip()
    return 5 if last_segment[:1].isupper() else 10


def _plan_to_dict(plan: Plan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "execution_id": plan.execution_id,
        "project": plan.project,
        "project_path": plan.project_path,
        "repo": plan.repo,
        "profile": plan.profile,
        "target": plan.target,
        "related_issue": plan.related_issue,
        "estimated_minutes": plan.estimated_minutes,
        "requires_confirmation": plan.requires_confirmation,
        "created_at": plan.created_at,
    }


def _validate_overrides(user_overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Allow-list check: only target/profile/issue_ref may be overridden."""
    if not user_overrides:
        return {}
    illegal = sorted(set(user_overrides) - ALLOWED_OVERRIDE_FIELDS)
    if illegal:
        raise HTTPException(
            status_code=400,
            detail=(
                f"user_overrides contains illegal fields: {illegal};"
                f" only {sorted(ALLOWED_OVERRIDE_FIELDS)} are allowed"
            ),
        )
    return {key: value for key, value in user_overrides.items() if value is not None}


@router.post("/api/v1/execution/plan", operation_id="execution_plan")
def create_plan(payload: ExecutionPlanRequest) -> dict[str, Any]:
    """Turn a DiscoveryResult into an inert execution Plan.

    Nothing runs here. The plan is stored in memory until it is confirmed
    (or forgotten on process restart).
    """
    discovery = payload.discovery or {}
    overrides = _validate_overrides(payload.user_overrides)

    target = str(
        overrides.get("target") or discovery.get("recommended_target") or ""
    ).strip()
    if not target:
        raise HTTPException(
            status_code=400,
            detail=(
                "discovery has no target: pass a DiscoveryResult with"
                " recommended_target (a failing test) or set user_overrides.target"
            ),
        )
    profile = str(
        overrides.get("profile")
        or discovery.get("recommended_profile")
        or DEFAULT_PROFILE
    ).strip()
    project_path = str(discovery.get("project_path") or "").strip()
    if not project_path:
        raise HTTPException(
            status_code=400,
            detail="discovery.project_path is required to launch a loop run",
        )
    project = (
        str(discovery.get("project_name") or "").strip()
        or project_path.rstrip("/").rsplit("/", 1)[-1]
    )
    repo = discovery.get("repo_url") or None
    issue_ref = overrides.get("issue_ref") or None
    if issue_ref is not None:
        issue_ref = str(issue_ref)

    plan_id = _new_plan_id()
    execution_id = _new_execution_id()
    try:
        run_request = LoopRunRequest(
            workspace_id=project,  # registry metadata only, never in argv
            project_path=project_path,
            profile_name=profile,
            target=target,
            issue_ref=issue_ref,
            execution_id=execution_id,
            dry_run=True,  # one bounded pass: the safe launch mode
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    plan = Plan(
        plan_id=plan_id,
        execution_id=execution_id,
        project=project,
        project_path=project_path,
        repo=repo,
        profile=profile,
        target=target,
        related_issue=issue_ref,
        estimated_minutes=_estimate_minutes(target),
        requires_confirmation=True,
        created_at=utcnow(),
        run_request=run_request,
    )
    with _PLANS_LOCK:
        _PLANS[plan_id] = plan
    return _plan_to_dict(plan)


@router.get("/api/v1/execution/plan/{plan_id}", operation_id="execution_get_plan")
def get_plan(plan_id: str) -> dict[str, Any]:
    plan = _PLANS.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"plan not found: {plan_id}")
    return _plan_to_dict(plan)


@router.post("/api/v1/execution/confirm", operation_id="execution_confirm")
def confirm_plan(payload: ConfirmRequest) -> dict[str, Any]:
    """Launch the confirmed plan's loop run. One-shot: the plan is consumed.

    The plan is popped atomically BEFORE start_loop so a concurrent or
    repeated confirm can never double-launch. start_loop() runs inside
    loops._GUARD() (defense in depth against bypasses elsewhere).
    """
    with _PLANS_LOCK:
        plan = _PLANS.pop(payload.plan_id, None)
        if plan is None:
            raise HTTPException(
                status_code=404,
                detail=f"plan not found or already confirmed: {payload.plan_id}",
            )
        try:
            with _GUARD():
                # Reuses plan.execution_id; if it were somehow blank,
                # start_loop's own fallback generates a fresh one.
                return start_loop(plan.run_request)
        except CONTROL_ERRORS as exc:
            raise_http(exc)
