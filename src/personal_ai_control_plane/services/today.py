from __future__ import annotations

from datetime import datetime
from typing import Any

from ..errors import ControlPlaneError
from ..integrations.multica import MulticaClient
from ..loops import active_loops
from .contexts import list_contexts
from .multica_work import context_issues

_PRIORITY = {
    "urgent": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "no_priority": 4,
    "p0": 0,
    "p1": 1,
    "p2": 2,
    "p3": 3,
}
_STATUS = {
    "blocked": 0,
    "in_progress": 1,
    "in_review": 2,
    "todo": 3,
    "backlog": 4,
    "done": 9,
    "closed": 9,
    "canceled": 9,
    "cancelled": 9,
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


def _norm(v: Any) -> str:
    if isinstance(v, dict):
        for key in ("name", "key", "value", "label"):
            if v.get(key) is not None:
                return str(v[key]).casefold()
    return str(v or "").casefold()


def _issue_key(issue: dict[str, Any]) -> str | None:
    for key in ("key", "issue_key", "identifier", "id"):
        if issue.get(key):
            return str(issue[key])
    return None


def _sort_key(issue: dict[str, Any]):
    return (
        _STATUS.get(_norm(issue.get("status")), 5),
        _PRIORITY.get(_norm(issue.get("priority")), 5),
        str(issue.get("due_date") or issue.get("dueDate") or "9999-12-31"),
    )


def build_today(client: MulticaClient | None = None) -> dict[str, Any]:
    client = client or MulticaClient()
    contexts = list_contexts()
    all_items, errors = [], []
    seen = set()

    for ctx in contexts:
        context_id = ctx["id"]
        try:
            result = context_issues(context_id, client=client)
            for binding in result.get("bindings", []):
                for issue in _items(binding.get("issues")):
                    status = _norm(issue.get("status"))
                    if status in {"done", "closed", "canceled", "cancelled"}:
                        continue
                    issue_key = _issue_key(issue) or repr(issue)
                    dedup = (binding.get("workspace_id"), issue_key)
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    all_items.append(
                        {
                            "context_id": context_id,
                            "context_name": ctx["name"],
                            "binding_id": binding["binding_id"],
                            "binding_role": binding["role"],
                            "workspace": binding["workspace"],
                            "project": binding["project"],
                            **issue,
                        }
                    )
            errors.extend(
                {"context_id": context_id, **x} for x in result.get("errors", [])
            )
        except ControlPlaneError as exc:
            errors.append({"context_id": context_id, "error": str(exc)})

    all_items.sort(key=_sort_key)
    focus = [
        {
            "context_id": x.get("context_id"),
            "context_name": x.get("context_name"),
            "binding_role": x.get("binding_role"),
            "workspace": x.get("workspace"),
            "project": x.get("project"),
            "issue": _issue_key(x),
            "title": x.get("title") or x.get("name"),
            "status": x.get("status"),
            "priority": x.get("priority"),
            "assignee": x.get("assignee"),
            "due_date": x.get("due_date") or x.get("dueDate"),
        }
        for x in all_items[:20]
    ]

    return {
        "date": datetime.now().astimezone().date().isoformat(),
        "configured_contexts": len(contexts),
        "open_issues": len(all_items),
        "blocked": sum(1 for x in all_items if _norm(x.get("status")) == "blocked"),
        "in_progress": sum(
            1 for x in all_items if _norm(x.get("status")) == "in_progress"
        ),
        "focus": focus,
        "active_loops": active_loops(),
        "errors": errors,
    }
