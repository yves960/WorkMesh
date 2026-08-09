from fastapi import APIRouter

from .http_errors import CONTROL_ERRORS, raise_http
from .models import ContextBriefRequest
from .services.brief import context_brief
from .services.contexts import list_contexts
from .services.multica_work import get_issue, get_issue_runs, get_run_messages
from .services.today import build_today

router = APIRouter()


@router.get("/agent/contexts", include_in_schema=False)
def agent_list_contexts():
    return {
        "contexts": [
            {
                "context_id": item["id"],
                "name": item["name"],
                "aliases": item.get("aliases", []),
            }
            for item in list_contexts()
        ]
    }


@router.post("/agent/context-brief", include_in_schema=False)
def agent_context_brief(payload: ContextBriefRequest):
    try:
        return context_brief(payload.text)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@router.get("/agent/today", include_in_schema=False)
def agent_today():
    try:
        return build_today()
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@router.get("/agent/issues/{issue_id}", include_in_schema=False)
def agent_issue_detail(issue_id: str, context_id: str | None = None):
    try:
        return get_issue(issue_id, context_id=context_id)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@router.get("/agent/issues/{issue_id}/runs", include_in_schema=False)
def agent_issue_runs(issue_id: str, context_id: str | None = None):
    try:
        return get_issue_runs(issue_id, context_id=context_id)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@router.get("/agent/runs/{task_id}/messages", include_in_schema=False)
def agent_run_messages(task_id: str, context_id: str | None = None):
    try:
        return get_run_messages(task_id, context_id=context_id)
    except CONTROL_ERRORS as exc:
        raise_http(exc)
