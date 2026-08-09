from __future__ import annotations

from pathlib import Path
from typing import Any

from ..errors import ControlPlaneError
from ..integrations.multica import MulticaClient
from ..integrations.openwebui import OpenWebUIClient
from .contexts import get_context
from .multica_work import resolve_workspace_id


def _items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for key in ("resources", "items", "data", "results"):
            value = raw.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _normalize_resource(resource: dict[str, Any]) -> dict[str, Any]:
    rtype = str(resource.get("resource_type") or resource.get("type") or "unknown")
    raw_ref = resource.get("resource_ref")
    ref: dict[str, Any] = {}
    if isinstance(raw_ref, dict):
        ref = raw_ref
    item = {
        "id": resource.get("id"),
        "resource_type": rtype,
        "resource_ref": ref,
        "source": "multica_project_resource",
    }
    if rtype == "local_directory":
        path = ref.get("local_path")
        item["local_path"] = path
        item["daemon_id"] = ref.get("daemon_id")
        item["label"] = ref.get("label")
        item["path_exists_on_control_plane_host"] = bool(
            path and Path(str(path)).exists()
        )
        # A usable Open WebUI Computer workspace candidate only means the path is
        # reachable on this same host. Control Plane does not create/switch the workspace.
        item["openwebui_computer_workspace_candidate"] = bool(
            path and Path(str(path)).is_dir()
        )
    elif rtype == "github_repo":
        item["url"] = ref.get("url")
        item["ref"] = ref.get("ref")
        item["default_branch_hint"] = ref.get("default_branch_hint")
    return item


def project_resources_for_binding(
    binding: dict[str, Any], client: MulticaClient | None = None
) -> dict[str, Any]:
    client = client or MulticaClient()
    workspace_id = resolve_workspace_id(
        binding["workspace"], profile=binding.get("profile"), client=client
    )
    raw = client.project_resources(
        binding["project"], workspace_id=workspace_id, profile=binding.get("profile")
    )
    resources = [_normalize_resource(x) for x in _items(raw)]
    return {
        "binding_id": binding["id"],
        "role": binding["role"],
        "workspace": binding["workspace"],
        "workspace_id": workspace_id,
        "project": binding["project"],
        "resources": resources,
    }


def context_resources(
    context_id: str, client: MulticaClient | None = None
) -> dict[str, Any]:
    ctx = get_context(context_id)
    client = client or MulticaClient()
    results, errors = [], []
    for binding in ctx.get("multica_bindings", []):
        try:
            results.append(project_resources_for_binding(binding, client=client))
        except ControlPlaneError as exc:
            errors.append(
                {
                    "binding_id": binding.get("id"),
                    "workspace": binding.get("workspace"),
                    "project": binding.get("project"),
                    "error": str(exc),
                }
            )
    return {"context_id": context_id, "bindings": results, "errors": errors}


def effective_context(
    context_id: str,
    *,
    verify_openwebui: bool = False,
    multica_client: MulticaClient | None = None,
    openwebui_client: OpenWebUIClient | None = None,
) -> dict[str, Any]:
    ctx = get_context(context_id)
    resources = context_resources(context_id, client=multica_client)
    kb = []
    owui = openwebui_client or OpenWebUIClient()
    for binding in ctx.get("openwebui_knowledge_bindings", []):
        item = dict(binding)
        item["source"] = "openwebui_knowledge"
        if verify_openwebui:
            item["verification"] = owui.knowledge_get(binding["knowledge_id"])
        kb.append(item)
    return {
        **ctx,
        "multica_project_resources": resources,
        "openwebui_knowledge": kb,
        "ownership": {
            "work_resources": "multica",
            "knowledge": "openwebui",
            "context_bindings_and_routing": "control_plane",
        },
    }
