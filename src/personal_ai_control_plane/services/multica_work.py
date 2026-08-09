from __future__ import annotations

from typing import Any

from ..errors import ControlPlaneError, MulticaCommandError, WorkspaceSelectionRequired
from ..integrations.multica import MulticaClient
from .contexts import configured_multica_bindings, get_context


def _items(raw: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for key in keys:
            value = raw.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _workspace_id(item: dict[str, Any]) -> str | None:
    for key in ("id", "workspace_id", "workspaceId", "uuid"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _workspace_label(item: dict[str, Any]) -> str:
    return str(
        item.get("slug") or item.get("name") or _workspace_id(item) or "<unknown>"
    )


def list_workspaces(
    client: MulticaClient | None = None, *, profile: str | None = None
) -> Any:
    return (client or MulticaClient()).workspaces(profile=profile)


def resolve_workspace_id(
    workspace: str | None,
    *,
    profile: str | None = None,
    client: MulticaClient | None = None,
) -> str:
    client = client or MulticaClient()
    if workspace:
        data = client.workspace_get(workspace, profile=profile)
        if isinstance(data, dict):
            candidate: dict[str, Any] = data
            workspace_value = data.get("workspace")
            if isinstance(workspace_value, dict):
                candidate = workspace_value
            wid = _workspace_id(candidate)
            if wid:
                return wid
        raise WorkspaceSelectionRequired(
            f"Could not resolve Multica workspace '{workspace}' to an id."
        )

    raw = client.workspaces(profile=profile)
    items = _items(raw, ("workspaces", "items", "data", "results"))
    if len(items) == 1:
        wid = _workspace_id(items[0])
        if wid:
            return wid
    if not items:
        raise WorkspaceSelectionRequired(
            "No Multica workspace was found. Run `multica workspace list`."
        )
    labels = ", ".join(_workspace_label(x) for x in items[:10])
    raise WorkspaceSelectionRequired(
        "Multiple Multica workspaces are available and no workspace was specified. "
        f"Available: {labels}. Specify a workspace explicitly."
    )


def list_projects(
    workspace: str | None = None,
    *,
    profile: str | None = None,
    client: MulticaClient | None = None,
) -> dict[str, Any]:
    client = client or MulticaClient()
    workspace_id = resolve_workspace_id(workspace, profile=profile, client=client)
    return {
        "workspace_id": workspace_id,
        "projects": client.projects(workspace_id=workspace_id, profile=profile),
    }


def _binding_result(
    binding: dict[str, Any], *, limit: int, client: MulticaClient
) -> dict[str, Any]:
    workspace_id = resolve_workspace_id(
        binding["workspace"], profile=binding.get("profile"), client=client
    )
    issues = client.issues(
        project=binding["project"],
        limit=limit,
        workspace_id=workspace_id,
        profile=binding.get("profile"),
    )
    return {
        "binding_id": binding["id"],
        "role": binding["role"],
        "workspace": binding["workspace"],
        "workspace_id": workspace_id,
        "project": binding["project"],
        "issues": issues,
    }


def context_issues(
    context_id: str,
    *,
    limit: int = 100,
    binding_id: int | None = None,
    client: MulticaClient | None = None,
) -> dict[str, Any]:
    ctx = get_context(context_id)
    bindings = ctx.get("multica_bindings", [])
    if binding_id is not None:
        bindings = [x for x in bindings if x["id"] == binding_id]
    if not bindings:
        return {
            "context_id": context_id,
            "bindings": [],
            "warning": "No matching Multica binding configured",
        }

    client = client or MulticaClient()
    results, errors = [], []
    for binding in bindings:
        try:
            results.append(_binding_result(binding, limit=limit, client=client))
        except ControlPlaneError as exc:
            errors.append(
                {
                    "binding_id": binding["id"],
                    "workspace": binding["workspace"],
                    "project": binding["project"],
                    "error": str(exc),
                }
            )
    return {"context_id": context_id, "bindings": results, "errors": errors}


def get_issue(
    issue_id: str,
    *,
    context_id: str | None = None,
    binding_id: int | None = None,
    client: MulticaClient | None = None,
) -> Any:
    client = client or MulticaClient()
    candidates = configured_multica_bindings(context_id)
    if not candidates:
        workspace_id = resolve_workspace_id(None, client=client)
        return client.issue_get(issue_id, workspace_id=workspace_id)

    if binding_id is not None:
        candidates = [
            (context, binding)
            for context, binding in candidates
            if binding["id"] == binding_id
        ]
    if not candidates:
        raise WorkspaceSelectionRequired(
            f"Context '{context_id}' has no matching Multica binding"
        )

    errors = []
    for context, binding in candidates:
        try:
            workspace_id = resolve_workspace_id(
                binding["workspace"], profile=binding.get("profile"), client=client
            )
            return {
                "context": {"context_id": context["id"], "name": context["name"]},
                "binding": binding,
                "issue": client.issue_get(
                    issue_id, workspace_id=workspace_id, profile=binding.get("profile")
                ),
            }
        except MulticaCommandError as exc:
            errors.append(str(exc))
    raise MulticaCommandError(
        ["multica", "issue", "get", issue_id], "; ".join(errors) or "Issue not found"
    )


def get_issue_runs(
    issue_id: str,
    *,
    context_id: str | None = None,
    binding_id: int | None = None,
    client: MulticaClient | None = None,
) -> Any:
    client = client or MulticaClient()
    candidates = configured_multica_bindings(context_id)
    if not candidates:
        workspace_id = resolve_workspace_id(None, client=client)
        return client.issue_runs(issue_id, workspace_id=workspace_id)

    if binding_id is not None:
        candidates = [
            (context, binding)
            for context, binding in candidates
            if binding["id"] == binding_id
        ]
    errors = []
    for context, binding in candidates:
        try:
            workspace_id = resolve_workspace_id(
                binding["workspace"], profile=binding.get("profile"), client=client
            )
            return {
                "context": {"context_id": context["id"], "name": context["name"]},
                "binding": binding,
                "runs": client.issue_runs(
                    issue_id, workspace_id=workspace_id, profile=binding.get("profile")
                ),
            }
        except MulticaCommandError as exc:
            errors.append(str(exc))
    raise MulticaCommandError(
        ["multica", "issue", "runs", issue_id], "; ".join(errors) or "Issue not found"
    )


def get_run_messages(
    task_id: str,
    *,
    context_id: str | None = None,
    binding_id: int | None = None,
    client: MulticaClient | None = None,
) -> Any:
    client = client or MulticaClient()
    candidates = configured_multica_bindings(context_id)
    if not candidates:
        workspace_id = resolve_workspace_id(None, client=client)
        return client.issue_run_messages(task_id, workspace_id=workspace_id)

    if binding_id is not None:
        candidates = [
            (context, binding)
            for context, binding in candidates
            if binding["id"] == binding_id
        ]
    if not candidates:
        raise WorkspaceSelectionRequired(
            f"Context '{context_id}' has no matching Multica binding"
        )

    errors = []
    for context, binding in candidates:
        try:
            workspace_id = resolve_workspace_id(
                binding["workspace"], profile=binding.get("profile"), client=client
            )
            return {
                "context": {"context_id": context["id"], "name": context["name"]},
                "binding": binding,
                "task_id": task_id,
                "messages": client.issue_run_messages(
                    task_id, workspace_id=workspace_id, profile=binding.get("profile")
                ),
            }
        except MulticaCommandError as exc:
            errors.append(str(exc))
    raise MulticaCommandError(
        ["multica", "issue", "run-messages", task_id],
        "; ".join(errors) or "Task run not found",
    )
