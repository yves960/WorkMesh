"""cptr bridge server.

Skeleton OpenAPI tool server (default port 8766) that:
  1. serves /openapi.json — pulled live from AICP ``/openwebui/openapi.json``
     and transformed (only the 6 read-only /agent/* tools, bridge branding);
     falls back to the bundled ``openapi.json`` snapshot when AICP is down.
  2. proxies tool calls verbatim to AICP with httpx — no extra logic.

No AICP source code is modified; the upstream is called over HTTP only.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .admin import router as admin_router

AICP_URL = os.environ.get("AICP_URL", "http://127.0.0.1:8765").rstrip("/")
BRIDGE_PORT = int(os.environ.get("CPTR_BRIDGE_PORT", "8766"))
TOOL_PATH_PREFIX = "/agent/"
_BUNDLED_SPEC = Path(__file__).with_name("openapi.json")

app = FastAPI(
    title="WorkMesh cptr Bridge",
    version="0.1.0",
    docs_url="/docs",
    openapi_url=None,  # we serve AICP's transformed spec at /openapi.json instead
)


def _transform_spec(spec: dict) -> dict:
    """Adapt the AICP spec for cptr registration: keep read-only agent tools only."""
    out = copy.deepcopy(spec)
    info = out.setdefault("info", {})
    info["title"] = "WorkMesh cptr Bridge"
    info["version"] = "0.1.0"
    info["description"] = (
        str(info.get("description") or "")
        + " Bridged by cptr-bridge skeleton; calls are proxied to the AICP upstream."
    ).strip()
    out["paths"] = {
        path: item
        for path, item in dict(out.get("paths") or {}).items()
        if path.startswith(TOOL_PATH_PREFIX)
    }
    out.pop("servers", None)
    return out


async def _load_spec() -> dict:
    """Pull the spec from AICP live; fall back to the bundled snapshot."""
    try:
        # trust_env=False: never route localhost AICP calls through the
        # system proxy (macOS scproxy otherwise hijacks 127.0.0.1 traffic).
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            response = await client.get(f"{AICP_URL}/openwebui/openapi.json")
            response.raise_for_status()
            return _transform_spec(response.json())
    except Exception:
        return _transform_spec(json.loads(_BUNDLED_SPEC.read_text(encoding="utf-8")))


@app.get("/openapi.json", include_in_schema=False)
async def openapi_spec() -> JSONResponse:
    return JSONResponse(await _load_spec())


@app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"status": "ok", "a_icp_url": AICP_URL}


# Admin UI (profiles / routing / forge-loop) must be registered BEFORE the
# catch-all proxy below, otherwise /admin/* would be forwarded to AICP.
app.include_router(admin_router)


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    include_in_schema=False,
)
async def proxy_to_aicp(path: str, request: Request) -> Response:
    """Skeleton proxy: forward the call to AICP as-is (httpx direct connection)."""
    target = f"{AICP_URL}/{path}"
    body = await request.body()
    headers = {"content-type": request.headers.get("content-type", "application/json")}
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        upstream = await client.request(
            request.method,
            target,
            headers=headers,
            params=dict(request.query_params),
            content=body,
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=BRIDGE_PORT)


if __name__ == "__main__":
    main()
