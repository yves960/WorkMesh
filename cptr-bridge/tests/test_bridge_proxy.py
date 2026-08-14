"""Minimal E2E test: bridge serves a valid transformed spec and proxies to AICP.

Starts a stub AICP (stdlib HTTP server) + the real bridge subprocess, then
uses requests to hit the bridge over HTTP.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import requests

BRIDGE_PORT = 8766
BRIDGE = f"http://127.0.0.1:{BRIDGE_PORT}"
BUNDLED_SPEC = json.loads(
    (Path(__file__).parent.parent / "src" / "cptr_bridge" / "openapi.json").read_text()
)


class _StubAICP(BaseHTTPRequestHandler):
    """Stands in for the real AICP: serves its spec + two stub tool endpoints."""

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/openwebui/openapi.json":
            self._send(200, BUNDLED_SPEC)
        elif self.path.startswith("/agent/today"):
            self._send(200, {"ok": True, "source": "stub-aicp"})
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/agent/context-brief":
            self._send(200, {"ok": True, "source": "stub-aicp"})
        else:
            self._send(404, {"detail": "not found"})

    def log_message(self, *_args) -> None:
        pass


@pytest.fixture(scope="module")
def bridge_stack():
    stub = HTTPServer(("127.0.0.1", 0), _StubAICP)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    env = {
        **os.environ,
        "AICP_URL": f"http://127.0.0.1:{stub.server_port}",
        "CPTR_BRIDGE_PORT": str(BRIDGE_PORT),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "cptr_bridge.server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                if requests.get(f"{BRIDGE}/health", timeout=1).status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(0.2)
        else:
            raise RuntimeError("bridge did not come up")
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        stub.shutdown()


def test_openapi_spec_is_valid_and_has_six_tools(bridge_stack):
    response = requests.get(f"{BRIDGE}/openapi.json", timeout=5)
    assert response.status_code == 200
    spec = response.json()
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "WorkMesh cptr Bridge"
    agent_paths = [p for p in spec["paths"] if p.startswith("/agent/")]
    assert len(agent_paths) == 6


def test_proxy_get_tool(bridge_stack):
    response = requests.get(f"{BRIDGE}/agent/today", timeout=5)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "source": "stub-aicp"}


def test_proxy_post_tool(bridge_stack):
    response = requests.post(
        f"{BRIDGE}/agent/context-brief", json={"text": "status?"}, timeout=5
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "source": "stub-aicp"}


def test_proxy_passes_through_upstream_404(bridge_stack):
    response = requests.get(f"{BRIDGE}/agent/nope", timeout=5)
    assert response.status_code == 404
