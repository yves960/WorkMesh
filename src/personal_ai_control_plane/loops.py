"""Internal ForgeLoop run registry: in-memory active loops + SQLite history.

Endpoints in loop_routes.py are internal (mounted on the FastAPI main app, NOT
in the curated openwebui/openapi.json spec). Open WebUI agents must not see them.

ForgeLoop CLI contract (v0.1.8, verified against ForgeLoop/src/cli_parser.py):

    loop run (--test T | --target TARGET) [--max-iterations N]
             [--agent-command PATH] [--agent-protocol P] [--agent-arg A ...]
             [--maven PATH] [--require-clean] <profile-name>
    loop {status|cleanup} <run_id> ...
    loop config ... | loop run-config ...

The profile name is a REQUIRED positional; there is no --profile, --workspace,
--issue, or --dry-run flag. tests/test_loop_command_against_real_cli.py guards
this contract (CLI existence via `loop --help`; argv shape via mocked Popen).
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from .db import connect, init_db, utcnow
from .errors import ControlPlaneError, MissingTargetError
from .models import ExecutionRecord, LoopCallbackRequest, LoopRunRequest

# run_id -> run record. Keys with a leading "_" are process bookkeeping and are
# stripped from every snapshot.
_ACTIVE_LOOPS: dict[str, dict[str, Any]] = {}

#: Safety cap so a misbehaving profile can never loop unbounded on the real CLI.
FORGELOOP_MAX_ITERATIONS = 5


class UnknownLoopRun(ControlPlaneError):
    """Raised when a callback references a run that was never started here."""


def _validate_request(req: LoopRunRequest) -> None:
    """A loop run is meaningless without a target; reject before any spawn."""
    if not (req.target or "").strip():
        raise MissingTargetError("target is required")


def loop_executable() -> str:
    return os.getenv("AICP_LOOP_EXECUTABLE", "loop").strip()


def _new_execution_id() -> str:
    """exec-YYYYMMDD-XXXXXX (UTC date + 6 lowercase hex chars)."""
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"exec-{stamp}-{secrets.token_hex(3)}"


def _loop_command(req: LoopRunRequest, executable: str, execution_id: str) -> list[str]:
    """Build argv for the REAL ForgeLoop CLI (`loop run`, see module docstring).

    Mapping decisions (recorded for posterity):
    - profile_name -> REQUIRED positional (ForgeLoop has no --profile flag;
      missing profile is rejected instead of spawning a doomed process).
    - workspace_id -> NOT passed; ForgeLoop discovers state from the working
      directory. AICP only keeps it in its own registry/DB.
    - project_path -> NOT an argv element; used as Popen cwd so ForgeLoop runs
      inside the project it operates on.
    - target -> `--target`. ForgeLoop has no --issue flag and requires the
      --test|--target group; the explicit target is the execution goal and is
      mandatory. issue_ref, when present, is metadata stored on
      ExecutionRecord only and NEVER leaks into the argv.
    - execution_id -> `--execution-id`; WorkMesh owns the id so the ForgeLoop
      run.json / worktree branch / webhook payload can correlate back.
    - dry_run -> ForgeLoop has NO --dry-run. dry_run=True maps to
      `--max-iterations 1` (one bounded pass in a scratch worktree — this is
      NOT a no-op dry-run; the coding agent still runs once).
    - --max-iterations is always present and capped at
      FORGELOOP_MAX_ITERATIONS (5).
    """
    profile = (req.profile_name or "").strip()
    if not profile:
        raise ControlPlaneError(
            "profile_name is required: ForgeLoop takes the profile as a"
            " positional argument (there is no --profile flag)"
        )
    _validate_request(req)
    execution_id = (execution_id or "").strip()
    if not execution_id:
        raise ControlPlaneError("execution_id is required for every loop run")
    iterations = 1 if req.dry_run else FORGELOOP_MAX_ITERATIONS
    return [
        executable,
        "run",
        "--target",
        req.target.strip(),
        "--max-iterations",
        str(iterations),
        "--execution-id",
        execution_id,
        profile,
    ]


def _snapshot(run_id: str) -> dict[str, Any]:
    return {k: v for k, v in _ACTIVE_LOOPS[run_id].items() if not k.startswith("_")}


def active_loops() -> list[dict[str, Any]]:
    """JSON-safe snapshot of every tracked loop run (no subprocess involved)."""
    return [_snapshot(run_id) for run_id in _ACTIVE_LOOPS]


def reset_loops_for_tests() -> None:
    _ACTIVE_LOOPS.clear()


def _reap(run_id: str, process: subprocess.Popen[bytes]) -> None:
    try:
        exit_code = process.wait()
    except Exception:  # noqa: BLE001 - bookkeeping thread must never crash the app
        return
    record = _ACTIVE_LOOPS.get(run_id)
    if record is None or record.get("status") not in ("starting", "running"):
        return
    record["exit_code"] = exit_code
    # The ForgeLoop callback is authoritative for pass/fail; this only marks a
    # process that exited before any callback arrived.
    record["status"] = "exited" if exit_code == 0 else "crashed"
    record["updated_at"] = utcnow()


def _persist_start(
    run_id: str, req: LoopRunRequest, cmd: list[str], execution_id: str
) -> None:
    now = utcnow()
    execution = ExecutionRecord(
        execution_id=execution_id,
        project_path=req.project_path,
        profile_name=req.profile_name,
        target=req.target,
        issue_ref=req.issue_ref,
        status="running",
        created_at=now,
        updated_at=now,
    )
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO loop_runs (run_id, workspace_id, project_path, profile_name,"
            " issue_ref, dry_run, status, created_at, updated_at, execution_id)"
            " VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)",
            (
                run_id,
                req.workspace_id,
                req.project_path,
                req.profile_name,
                req.issue_ref,
                int(req.dry_run),
                now,
                now,
                execution_id,
            ),
        )
        conn.execute(
            "INSERT INTO execution_records (execution_id, project_path, profile_name,"
            " target, issue_ref, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'running', ?, ?)"
            " ON CONFLICT(execution_id) DO UPDATE SET"
            " status = 'running', updated_at = excluded.updated_at,"
            " project_path = excluded.project_path,"
            " profile_name = excluded.profile_name,"
            " target = excluded.target, issue_ref = excluded.issue_ref",
            (
                execution.execution_id,
                execution.project_path,
                execution.profile_name,
                execution.target,
                execution.issue_ref,
                execution.created_at,
                execution.updated_at,
            ),
        )
    _ = cmd  # command shape is kept in the in-memory snapshot only


def start_loop(req: LoopRunRequest) -> dict[str, Any]:
    """Launch `loop run` as a detached subprocess and return immediately."""
    _validate_request(req)
    executable = loop_executable()
    if shutil.which(executable) is None:
        raise ControlPlaneError(
            f"loop executable '{executable}' was not found on PATH"
            " (set AICP_LOOP_EXECUTABLE to override)"
        )

    execution_id = (req.execution_id or "").strip() or _new_execution_id()
    cmd = _loop_command(req, executable, execution_id)
    run_id = uuid.uuid4().hex
    now = utcnow()
    record: dict[str, Any] = {
        "run_id": run_id,
        "execution_id": execution_id,
        "status": "running",
        "workspace_id": req.workspace_id,
        "project_path": req.project_path,
        "profile_name": req.profile_name,
        "target": req.target,
        "issue_ref": req.issue_ref,
        "dry_run": req.dry_run,
        "command": cmd,
        "created_at": now,
        "updated_at": now,
    }
    try:
        process: subprocess.Popen[bytes] = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            shell=False,
            start_new_session=True,
            cwd=os.path.expanduser(req.project_path),  # ForgeLoop discovers the
            # project from its working directory; there is no --workspace flag.
        )
    except OSError as exc:
        raise ControlPlaneError(f"failed to launch loop run: {exc}") from exc

    record["_process"] = process
    _ACTIVE_LOOPS[run_id] = record
    _persist_start(run_id, req, cmd, execution_id)
    snapshot = _snapshot(run_id)  # captured before the reaper can flip status
    threading.Thread(
        target=_reap, args=(run_id, process), daemon=True, name=f"loop-reap-{run_id}"
    ).start()
    return snapshot


def _load_run_from_db(run_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT run_id, workspace_id, project_path, profile_name, issue_ref,"
            " dry_run, status, evidence_uri, diff_summary, created_at, updated_at,"
            " execution_id FROM loop_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["dry_run"] = bool(data.get("dry_run"))
    return data


def _load_run_by_execution_id(execution_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT run_id, workspace_id, project_path, profile_name, issue_ref,"
            " dry_run, status, evidence_uri, diff_summary, created_at, updated_at,"
            " execution_id FROM loop_runs WHERE execution_id = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (execution_id,),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["dry_run"] = bool(data.get("dry_run"))
    return data


def _resolve_callback_record(
    req: LoopCallbackRequest,
) -> tuple[str, dict[str, Any]]:
    """Resolve the callback target; execution_id preferred, run_id legacy."""
    if req.execution_id:
        for record in _ACTIVE_LOOPS.values():
            if record.get("execution_id") == req.execution_id:
                return record["run_id"], record
        revived = _load_run_by_execution_id(req.execution_id)
        if revived is None:
            raise UnknownLoopRun(f"unknown execution_id: {req.execution_id}")
        _ACTIVE_LOOPS[revived["run_id"]] = revived
        return revived["run_id"], revived
    # Legacy correlation key (deprecated): ForgeLoop run_id.
    record = _ACTIVE_LOOPS.get(req.run_id or "")
    if record is None:
        revived = _load_run_from_db(req.run_id or "")
        if revived is None:
            raise UnknownLoopRun(f"unknown run_id: {req.run_id}")
        record = revived
        _ACTIVE_LOOPS[req.run_id or ""] = record
    return req.run_id or "", record


def record_callback(req: LoopCallbackRequest) -> dict[str, Any]:
    """Apply a ForgeLoop completion callback: memory dict + SQLite updates.

    Correlation prefers `execution_id` (the stable cross-system key);
    `run_id` still works for legacy ForgeLoop versions (deprecated).
    """
    run_id, record = _resolve_callback_record(req)

    now = utcnow()
    record["status"] = req.status
    record["evidence_uri"] = req.evidence_uri
    record["diff_summary"] = req.diff_summary
    if req.profile_name:
        record["profile_name"] = req.profile_name
    record["completed_at"] = now
    record["updated_at"] = now

    execution_id = record.get("execution_id")
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO loop_runs (run_id, workspace_id, project_path, profile_name,"
            " issue_ref, dry_run, status, evidence_uri, diff_summary, created_at,"
            " updated_at, completed_at, execution_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(run_id) DO UPDATE SET"
            " status = excluded.status, evidence_uri = excluded.evidence_uri,"
            " diff_summary = excluded.diff_summary, profile_name = excluded.profile_name,"
            " updated_at = excluded.updated_at, completed_at = excluded.completed_at,"
            " execution_id = excluded.execution_id",
            (
                run_id,
                record.get("workspace_id") or "",
                record.get("project_path") or "",
                req.profile_name or record.get("profile_name"),
                record.get("issue_ref"),
                int(bool(record.get("dry_run"))),
                req.status,
                req.evidence_uri,
                req.diff_summary,
                record.get("created_at") or now,
                now,
                now,
                execution_id,
            ),
        )
        if execution_id:
            conn.execute(
                "UPDATE execution_records SET status = ?, updated_at = ?"
                " WHERE execution_id = ?",
                (req.status, now, execution_id),
            )
    return _snapshot(run_id)
