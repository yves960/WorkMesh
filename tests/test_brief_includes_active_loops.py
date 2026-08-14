"""brief and today must both embed the active_loops snapshot."""

from __future__ import annotations

from fastapi.testclient import TestClient

from personal_ai_control_plane import loops
from personal_ai_control_plane.api import app
from personal_ai_control_plane.integrations.multica import MulticaClient
from personal_ai_control_plane.models import ContextCreate, MulticaBindingInput
from personal_ai_control_plane.services.brief import context_brief
from personal_ai_control_plane.services.contexts import create_context
from personal_ai_control_plane.services.today import build_today


class FakeMultica(MulticaClient):
    def workspace_get(
        self, workspace_ref: str, *, profile: str | None = None
    ) -> dict[str, str]:
        return {"id": "ws-1"}

    def issues(self, **kwargs):
        return [
            {
                "key": "MUL-1",
                "title": "Active issue",
                "status": "in_progress",
                "priority": "high",
            }
        ]

    def project_resources(self, project_id, **kwargs):
        return []

    def project_get(self, project_id, **kwargs):
        return {"id": project_id, "title": "Asset Center V4", "status": "in_progress"}


class FakePopen:
    def __init__(self, cmd, **kwargs):
        self.pid = 1

    def wait(self, timeout=None):
        return 0


def _seed_loop(monkeypatch, tmp_path) -> str:
    monkeypatch.setattr(loops.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(loops.shutil, "which", lambda _name: "/usr/local/bin/loop")
    monkeypatch.setenv("AICP_LOOP_EXECUTABLE", "loop")
    loops.reset_loops_for_tests()
    client = TestClient(app)
    return client.post(
        "/api/v1/loop/run",
        json={
            "workspace_id": "ws-1",
            "project_path": "~/projects/self/WorkMesh",
            "profile_name": "sast-fixer",  # required positional in ForgeLoop CLI
            "issue_ref": "MUL-7",
        },
    ).json()["run_id"]


def test_context_brief_includes_active_loops(monkeypatch, tmp_path):
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    run_id = _seed_loop(monkeypatch, tmp_path)
    create_context(
        ContextCreate(
            id="asset-center",
            name="Asset Center",
            multica_bindings=[
                MulticaBindingInput(workspace="media", project="asset-v4")
            ],
        )
    )

    result = context_brief("Asset Center 现在怎么样", client=FakeMultica())

    assert result["matched"] is True
    loops_in_brief = result["active_loops"]
    assert isinstance(loops_in_brief, list) and len(loops_in_brief) == 1
    assert loops_in_brief[0]["run_id"] == run_id
    assert loops_in_brief[0]["issue_ref"] == "MUL-7"


def test_context_brief_unmatched_branch_also_includes_active_loops(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    run_id = _seed_loop(monkeypatch, tmp_path)

    result = context_brief("完全不存在的项目", client=FakeMultica())

    assert result["matched"] is False
    assert [x["run_id"] for x in result["active_loops"]] == [run_id]


def test_today_includes_active_loops(monkeypatch, tmp_path):
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    run_id = _seed_loop(monkeypatch, tmp_path)
    create_context(
        ContextCreate(
            id="asset-center",
            name="Asset Center",
            multica_bindings=[
                MulticaBindingInput(workspace="media", project="asset-v4")
            ],
        )
    )

    result = build_today(client=FakeMultica())

    assert [x["run_id"] for x in result["active_loops"]] == [run_id]
    assert result["open_issues"] == 1  # sanity: live work data still present
