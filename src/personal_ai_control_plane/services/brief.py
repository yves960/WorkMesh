from __future__ import annotations

from collections import Counter
from typing import Any

from ..errors import ContextNotFound, ControlPlaneError
from ..integrations.multica import MulticaClient
from ..loops import active_loops
from .contexts import get_context, list_contexts, resolve_context
from .multica_work import context_issues, resolve_workspace_id
from .resources import context_resources

_CLOSED = {"done", "closed", "completed", "canceled", "cancelled"}
_PRIORITY_ORDER = {
    "urgent": 0,
    "p0": 0,
    "high": 1,
    "p1": 1,
    "medium": 2,
    "p2": 2,
    "low": 3,
    "p3": 3,
}
_STATUS_ORDER = {
    "blocked": 0,
    "in_progress": 1,
    "in_review": 2,
    "todo": 3,
    "backlog": 4,
}


def _items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for key in ("issues", "items", "data", "results"):
            value = raw.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _norm(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "key", "value", "label"):
            if value.get(key) is not None:
                return str(value[key]).strip().casefold()
    return str(value or "").strip().casefold()


def _issue_key(issue: dict[str, Any]) -> str | None:
    for key in ("key", "issue_key", "identifier", "id"):
        value = issue.get(key)
        if value:
            return str(value)
    return None


def _issue_summary(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue": _issue_key(issue),
        "title": issue.get("title") or issue.get("name"),
        "status": issue.get("status"),
        "priority": issue.get("priority"),
        "assignee": issue.get("assignee"),
        "due_date": issue.get("due_date") or issue.get("dueDate"),
        "run_state": (
            issue.get("run_state")
            or issue.get("latest_run_status")
            or issue.get("run_status")
            or issue.get("task_status")
        ),
    }


def _sort_issue(issue: dict[str, Any]):
    return (
        _STATUS_ORDER.get(_norm(issue.get("status")), 5),
        _PRIORITY_ORDER.get(_norm(issue.get("priority")), 5),
        str(issue.get("due_date") or issue.get("dueDate") or "9999-12-31"),
    )


def _work_summary(raw_context_issues: dict[str, Any]) -> dict[str, Any]:
    flat: list[dict[str, Any]] = []
    binding_summaries = []
    for binding in raw_context_issues.get("bindings", []):
        items = _items(binding.get("issues"))
        binding_summaries.append(
            {
                "binding_id": binding.get("binding_id"),
                "role": binding.get("role"),
                "workspace": binding.get("workspace"),
                "workspace_id": binding.get("workspace_id"),
                "project": binding.get("project"),
                "issue_count": len(items),
            }
        )
        for issue in items:
            item = dict(issue)
            item["_binding"] = binding
            flat.append(item)

    open_items = [x for x in flat if _norm(x.get("status")) not in _CLOSED]
    open_items.sort(key=_sort_issue)
    status_counts = Counter(_norm(x.get("status")) or "unknown" for x in open_items)
    priority_counts = Counter(_norm(x.get("priority")) or "unknown" for x in open_items)

    focus = []
    for issue in open_items[:12]:
        b = issue.get("_binding", {})
        focus.append(
            {
                **_issue_summary(issue),
                "binding_role": b.get("role"),
                "workspace": b.get("workspace"),
                "project": b.get("project"),
            }
        )

    return {
        "open_issue_count": len(open_items),
        "blocked_count": status_counts.get("blocked", 0),
        "in_progress_count": status_counts.get("in_progress", 0),
        "status_counts": dict(status_counts),
        "priority_counts": dict(priority_counts),
        "focus": focus,
        "bindings": binding_summaries,
        "errors": raw_context_issues.get("errors", []),
    }


def _project_snapshots(
    ctx: dict[str, Any], client: MulticaClient
) -> list[dict[str, Any]]:
    snapshots = []
    for binding in ctx.get("multica_bindings", []):
        try:
            wid = resolve_workspace_id(
                binding["workspace"], profile=binding.get("profile"), client=client
            )
            project = client.project_get(
                binding["project"], workspace_id=wid, profile=binding.get("profile")
            )
            snapshots.append(
                {
                    "binding_id": binding["id"],
                    "role": binding["role"],
                    "workspace": binding["workspace"],
                    "workspace_id": wid,
                    "project_ref": binding["project"],
                    "project": project,
                }
            )
        except ControlPlaneError as exc:
            snapshots.append(
                {
                    "binding_id": binding.get("id"),
                    "role": binding.get("role"),
                    "workspace": binding.get("workspace"),
                    "project_ref": binding.get("project"),
                    "error": str(exc),
                }
            )
    return snapshots


def context_brief(text: str, *, client: MulticaClient | None = None) -> dict[str, Any]:
    """Build a live, read-only work brief for the context named in user text.

    Knowledge content is intentionally NOT queried here. Open WebUI already owns
    agentic Knowledge tools. We return the bound knowledge IDs and a precise next
    tool plan so the Open WebUI model can call query_knowledge_files itself.
    """
    try:
        resolved = resolve_context(text)
    except ContextNotFound:
        return {
            "matched": False,
            "query": text,
            "available_contexts": [
                {
                    "context_id": x["id"],
                    "name": x["name"],
                    "aliases": x.get("aliases", []),
                }
                for x in list_contexts()
            ],
            "active_loops": active_loops(),
            "next_action": "Choose the matching context from available_contexts or ask the user only if genuinely ambiguous.",
        }

    context_id = resolved["id"]
    ctx = get_context(context_id)
    client = client or MulticaClient()
    issues = context_issues(context_id, client=client)
    resources = context_resources(context_id, client=client)
    knowledge_ids = [
        x["knowledge_id"] for x in ctx.get("openwebui_knowledge_bindings", [])
    ]

    return {
        "matched": True,
        "query": text,
        "context": {
            "context_id": context_id,
            "name": ctx["name"],
            "aliases": ctx.get("aliases", []),
            "match_score": resolved.get("match_score"),
        },
        "projects": _project_snapshots(ctx, client),
        "work": _work_summary(issues),
        "project_resources": resources,
        "active_loops": active_loops(),
        "openwebui_knowledge": {
            "bindings": ctx.get("openwebui_knowledge_bindings", []),
            "knowledge_ids": knowledge_ids,
            "content_queried_by_control_plane": False,
        },
        "knowledge_query_plan": {
            "bound": bool(knowledge_ids),
            "tool": "query_knowledge_files"
            if knowledge_ids
            else "query_knowledge_bases",
            "query": text if knowledge_ids else ctx["name"],
            "knowledge_ids": knowledge_ids,
            "instruction": (
                "If the question needs design/spec/domain/history/decision knowledge, call Open WebUI's built-in "
                "query_knowledge_files with the original query and these knowledge_ids before answering."
                if knowledge_ids
                else "No Knowledge base is explicitly bound to this Context. If document knowledge is needed, use Open WebUI's "
                "query_knowledge_bases or search_knowledge_bases with the Context name to discover a relevant KB, then call "
                "query_knowledge_files. Do not ask Control Plane to retrieve or index Knowledge."
            ),
        },
        "response_guidance": [
            "Treat Multica data in this result as the live work-status source of truth.",
            "Treat Multica Project Resources as pointers; do not duplicate them into Control Plane storage.",
            "Use Open WebUI built-in Knowledge tools for bound knowledge bases when the question needs document evidence.",
            "This tool is read-only. Do not imply that an Issue, Task, Project, or Knowledge item was changed.",
        ],
    }
