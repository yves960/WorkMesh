"""Guard _loop_command() against the REAL ForgeLoop CLI contract.

Fork-bomb / agent-execution safety: these tests NEVER launch `loop run`.
- CLI existence is verified with `loop --help` only (argparse exits 0, no run).
- argv shape is asserted against a MOCKED subprocess.Popen.
- Generated flags are cross-checked against the real `loop run --help` text.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from personal_ai_control_plane import loops
from personal_ai_control_plane.errors import ControlPlaneError
from personal_ai_control_plane.models import LoopRunRequest

LOOP_BIN = shutil.which("loop") or str(
    Path.home() / "projects" / "self" / "ForgeLoop" / "loop"
)

cli_available = shutil.which("loop") is not None or Path(LOOP_BIN).exists()


class RecordingPopen:
    """Mocked Popen: records argv + kwargs, never spawns anything."""

    last_cmd: list[str] | None = None
    last_kwargs: dict[str, Any] | None = None

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.pid = 9109
        RecordingPopen.last_cmd = list(cmd)
        RecordingPopen.last_kwargs = kwargs

    def wait(self, timeout=None):
        return 0


def _req(**overrides) -> LoopRunRequest:
    base: dict[str, Any] = {
        "workspace_id": "ws-1",
        "project_path": "~/projects/demo",
        "profile_name": "sast-fixer",
        "issue_ref": "MUL-42",
        "dry_run": True,
    }
    base.update(overrides)
    return LoopRunRequest(**base)


def test_dry_run_argv_matches_real_cli_shape():
    cmd = loops._loop_command(_req(), "loop")
    assert cmd == [
        "loop",
        "run",
        "--target",
        "MUL-42",
        "--max-iterations",
        "1",
        "sast-fixer",
    ]


def test_no_phantom_flags_from_old_contract():
    cmd = loops._loop_command(_req(), "loop")
    for phantom in ("--workspace", "--project", "--issue", "--profile", "--dry-run", "--test"):
        assert phantom not in cmd
    assert cmd[-1] == "sast-fixer"  # profile is the trailing positional
    assert cmd[cmd.index("--max-iterations") + 1] == "1"


def test_non_dry_run_caps_iterations_at_safety_limit():
    cmd = loops._loop_command(_req(dry_run=False), "loop")
    assert cmd[cmd.index("--max-iterations") + 1] == str(loops.FORGELOOP_MAX_ITERATIONS)
    assert loops.FORGELOOP_MAX_ITERATIONS <= 5


def test_missing_profile_is_rejected_not_spawned():
    with pytest.raises(ControlPlaneError):
        loops._loop_command(_req(profile_name=None), "loop")
    with pytest.raises(ControlPlaneError):
        loops._loop_command(_req(profile_name="  "), "loop")


def test_start_loop_launches_with_project_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(loops.subprocess, "Popen", RecordingPopen)
    monkeypatch.setattr(loops.shutil, "which", lambda _n: "/usr/local/bin/loop")
    monkeypatch.setenv("AICP_LOOP_EXECUTABLE", "loop")
    loops.reset_loops_for_tests()

    snap = loops.start_loop(_req(project_path=str(tmp_path / "proj")))

    assert snap["status"] == "running"
    assert RecordingPopen.last_kwargs["cwd"] == str(tmp_path / "proj")
    assert RecordingPopen.last_cmd is not None
    assert RecordingPopen.last_cmd[1:3] == ["run", "--target"]
    loops.reset_loops_for_tests()


def test_start_loop_expands_tilde_in_cwd(monkeypatch):
    monkeypatch.setattr(loops.subprocess, "Popen", RecordingPopen)
    monkeypatch.setattr(loops.shutil, "which", lambda _n: "/usr/local/bin/loop")
    monkeypatch.setenv("AICP_LOOP_EXECUTABLE", "loop")
    loops.reset_loops_for_tests()

    loops.start_loop(_req())

    assert RecordingPopen.last_kwargs is not None
    assert RecordingPopen.last_kwargs["cwd"] == os.path.expanduser("~/projects/demo")
    assert "~" not in RecordingPopen.last_kwargs["cwd"]
    loops.reset_loops_for_tests()


@pytest.mark.skipif(not cli_available, reason="ForgeLoop CLI not installed")
def test_real_cli_exists_via_help_only():
    proc = subprocess.Popen([LOOP_BIN, "--help"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _err = proc.communicate(timeout=60)
    assert proc.returncode == 0
    for sub in ("run", "status", "submit", "cleanup", "config"):
        assert sub.encode() in out


@pytest.mark.skipif(not cli_available, reason="ForgeLoop CLI not installed")
def test_generated_flags_exist_in_real_run_help():
    proc = subprocess.Popen(
        [LOOP_BIN, "run", "--help"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    out, _err = proc.communicate(timeout=60)
    assert proc.returncode == 0
    for flag in ("--target", "--max-iterations", "profile"):
        assert flag.encode() in out
    assert b"--dry-run" not in out  # confirms our dry-run mapping decision
    assert b"--workspace" not in out
    assert b"--issue" not in out
