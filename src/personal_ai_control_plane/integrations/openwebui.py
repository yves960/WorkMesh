from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..settings import openwebui_api_key, openwebui_url


class OpenWebUIClient:
    """Thin verifier for existing Open WebUI assets. It does not implement RAG."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: int = 15,
    ):
        self.base_url = (base_url or openwebui_url() or "").rstrip("/")
        self.api_key = api_key or openwebui_api_key()
        self.timeout_seconds = timeout_seconds

    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def knowledge_get(self, knowledge_id: str) -> Any:
        if not self.configured():
            return {
                "configured": False,
                "knowledge_id": knowledge_id,
                "note": "Set OPEN_WEBUI_URL and OPEN_WEBUI_API_KEY to verify this binding. Retrieval remains in Open WebUI.",
            }
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/knowledge/{knowledge_id}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body) if body else None
                return {"configured": True, "ok": True, "knowledge": data}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return {
                "configured": True,
                "ok": False,
                "status": exc.code,
                "error": body[:1000],
            }
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            return {"configured": True, "ok": False, "error": str(exc)}
