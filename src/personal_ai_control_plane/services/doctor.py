from __future__ import annotations

from typing import Any

from ..errors import ControlPlaneError
from ..integrations.multica import MulticaClient
from ..integrations.openwebui import OpenWebUIClient
from ..settings import database_path
from .contexts import list_contexts


def doctor() -> dict[str, Any]:
    client = MulticaClient()
    owui = OpenWebUIClient()
    errors: list[str] = []
    result: dict[str, Any] = {
        "multica_on_path": client.available(),
        "openwebui_api_configured": owui.configured(),
        "database": str(database_path()),
        "contexts": len(list_contexts()),
        "ok": True,
        "errors": errors,
    }
    if not client.available():
        result["ok"] = False
        errors.append("multica executable not found on PATH")
        return result
    for name, fn in (
        ("version", client.version),
        ("auth_status", client.auth_status),
        ("daemon_status", client.daemon_status),
    ):
        try:
            result[name] = fn()
        except ControlPlaneError as exc:
            result["ok"] = False
            errors.append(f"{name}: {exc}")
    return result
