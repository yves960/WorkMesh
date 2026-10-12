"""Shared fixtures for workflow tests: isolated root, demo project, HTTP+CLI mocks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from personal_ai_control_plane import execution_routes, loops
from personal_ai_control_plane.api import app
from personal_ai_control_plane.workflow import loader, steps
from personal_ai_control_plane.workflow_routes import reset_workflow_routes_for_tests

REPO_ROOT = Path(__file__).resolve().parents[1]
WF_EXEC_ID_RE = re.compile(r"^wf-\d{8}-[0-9a-f]{6}$")
LOOP_EXEC_ID_RE = re.compile(r"^exec-\d{8}-[0-9a-f]{6}$")

TRIGGER: dict[str, Any] = {
    "source": "open_webui",
    "user_message": "UT 挂了，帮我修",
    "project_path": "",  # filled per-test with the demo project parent
}


class FakePopen:
    """Mocked Popen: records argv, never spawns. Also carries the
    context-manager/communicate surface so discovery's `subprocess.run` git
    probe keeps working with subprocess.Popen patched on the shared module."""

    last_cmd: list[str] | None = None

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.args = cmd
        self.pid = 4242
        self.returncode = 0
        FakePopen.last_cmd = list(cmd)

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return self.returncode

    def communicate(self, input=None, timeout=None):
        return (b"", b"")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_project(parent: Path) -> Path:
    """Demo Maven project with one failing surefire test (no mvn needed)."""
    project = parent / "demo-java-ut"
    (project / "src").mkdir(parents=True, exist_ok=True)
    (project / "pom.xml").write_text(
        "<project><groupId>com.demo</groupId>"
        "<artifactId>demo</artifactId><version>0.0.1</version></project>"
    )
    reports = project / "target" / "surefire-reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "TEST-com.demo.X.txt").write_text(
        "Tests run: 2, Failures: 1, Errors: 0, Skipped: 0\n"
        "testLogin(com.demo.X)  Time elapsed: 0.01 s  <<< FAILURE!\n"
    )
    return project


def write_workflow(root: Path, name: str, content: str) -> None:
    (root / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "workflows" / f"{name}.yaml").write_text(content, encoding="utf-8")


@pytest.fixture()
def wf_env(tmp_path, monkeypatch) -> dict[str, Any]:
    """Isolated AICP_ROOT + java-ut-repair.yaml + demo project + all mocks."""
    root = tmp_path / "root"
    root.mkdir()
    write_workflow(root, "java-ut-repair", (REPO_ROOT / "workflows" / "java-ut-repair.yaml").read_text())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = make_project(workspace)

    monkeypatch.setenv("AICP_ROOT", str(root))
    monkeypatch.setattr(steps, "_make_client", lambda: httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ))
    monkeypatch.setattr(loops.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(loops.shutil, "which", lambda _n: "/usr/local/bin/loop")
    monkeypatch.setenv("AICP_LOOP_EXECUTABLE", "loop")

    loader.clear_loader_cache()
    loops.reset_loops_for_tests()
    execution_routes.reset_plans_for_tests()
    reset_workflow_routes_for_tests()
    yield {"root": root, "workspace": workspace, "project": project}
    loader.clear_loader_cache()
    reset_workflow_routes_for_tests()


def trigger_for(project: Path) -> dict[str, Any]:
    return {**TRIGGER, "project_path": str(project)}
