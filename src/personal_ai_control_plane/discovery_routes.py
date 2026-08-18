"""Task 2: POST /api/v1/discovery — vague user message -> DiscoveryResult."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .discovery import discover_from_user_message

router = APIRouter()


class DiscoveryRequest(BaseModel):
    user_message: str = Field(default="", description="Raw user message, e.g. 'UT 挂了'")
    workspace_root: str = Field(default="", description="Directory to scan for a project")
    max_scan_depth: int = Field(default=5, ge=0, le=20)


@router.post("/api/v1/discovery", operation_id="discovery_from_user_message")
def discovery(payload: DiscoveryRequest) -> dict:
    """Keyword-intent + workspace-scan + surefire-report discovery.

    No LLM calls; `mvn test` is only a 60s-timeout fallback and never raises.
    """
    user_message = (payload.user_message or "").strip()
    workspace_root = (payload.workspace_root or "").strip()
    if not user_message or not workspace_root:
        raise HTTPException(status_code=400, detail="user_message and workspace_root required")
    try:
        result = discover_from_user_message(
            user_message, workspace_root, max_scan_depth=payload.max_scan_depth
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="no project found under workspace_root")
    return {
        "project_path": result.project_path,
        "project_name": result.project_name,
        "build_tool": result.build_tool,
        "repo_url": result.repo_url,
        "language": result.language,
        "failing_tests": result.failing_tests,
        "failure_summary": result.failure_summary,
        "recommended_target": result.recommended_target,
        "recommended_profile": result.recommended_profile,
        "discovery_confidence": result.discovery_confidence,
    }
