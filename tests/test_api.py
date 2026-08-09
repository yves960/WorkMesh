from fastapi.testclient import TestClient

from personal_ai_control_plane.api import app


def test_health(tmp_path, monkeypatch):
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    c = TestClient(app)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["version"] == "0.4.0"
    assert r.json()["principle"] == "bind_and_route_do_not_duplicate"
