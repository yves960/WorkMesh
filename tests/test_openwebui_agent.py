from typing import Never

from fastapi.testclient import TestClient

from personal_ai_control_plane import agent_routes
from personal_ai_control_plane.api import app
from personal_ai_control_plane.errors import ContextNotFound


def test_agent_spec_is_read_only():
    c = TestClient(app)
    r = c.get("/openwebui/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["info"]["version"] == "0.4.0"
    paths = spec["paths"]
    assert "/agent/context-brief" in paths
    assert "/agent/today" in paths
    assert "/agent/runs/{task_id}/messages" in paths
    assert "/contexts" not in paths
    assert all("delete" not in item and "patch" not in item for item in paths.values())
    assert {
        operation[method]["operationId"]
        for operation in paths.values()
        for method in operation
    } == {
        "workspace_context_brief",
        "workspace_list_contexts",
        "workspace_today",
        "workspace_issue_detail",
        "workspace_issue_runs",
        "workspace_run_messages",
    }


def test_agent_routes_remain_registered(tmp_path, monkeypatch):
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    client = TestClient(app)

    response = client.get("/agent/contexts")

    assert response.status_code == 200
    assert response.json() == {"contexts": []}


def test_agent_route_preserves_not_found_mapping(monkeypatch):
    def missing_issue(issue_id: str, context_id: str | None = None) -> Never:
        raise ContextNotFound(issue_id)

    monkeypatch.setattr(agent_routes, "get_issue", missing_issue)
    client = TestClient(app)

    response = client.get("/agent/issues/MUL-404")

    assert response.status_code == 404
    assert response.json() == {"detail": "MUL-404"}
