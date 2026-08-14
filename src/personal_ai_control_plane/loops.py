"""Internal ForgeLoop run registry: in-memory active loops + SQLite history.

Endpoints in loop_routes.py are internal (mounted on the FastAPI main app, NOT
in the curated openwebui/openapi.json spec). Open WebUI agents must not see them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import uuid
from typing import Any

from .db import connect, init_db, utcnow
from .errors import ControlPlaneError
from .models import LoopCallbackRequest, LoopRunRequest

# run_id -> run record. Keys with a leading "_" are process bookkeeping and are
# stripped from every snapshot.
_ACTIVE_LOOPS: dict[str, dict[str, Any]] = {}


class UnknownLoopRun(ControlPlaneError):
    """Raised when a callback references a run that was never started here."""


def loop_executable() -> str:
    return os.getenv("AICP_LOOP_EXECUTABLE", "loop").strip()


def _loop_command(req: LoopRunRequest, executable: str) -> list[str]:
    cmd = [
        executable,
        "run",
        "--workspace",
        req.workspace_id,
        "--project",
        req.project_path,
        "--issue",
        req.issue_ref,
    ]
    if req.profile_name:
        cmd += ["--profile", req.profile_name]
    if req.dry_run:
        cmd.append("--dry-run")
    return cmd


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


def _persist_start(run_id: str, req: LoopRunRequest, cmd: list[str]) -> None:
    now = utcnow()
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO loop_runs (run_id, workspace_id, project_path, profile_name,"
            " issue_ref, dry_run, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)",
            (
                run_id,
                req.workspace_id,
                req.project_path,
                req.profile_name,
                req.issue_ref,
                int(req.dry_run),
                now,
                now,
            ),
        )
    _ = cmd  # command shape is kept in the in-memory snapshot only


def start_loop(req: LoopRunRequest) -> dict[str, Any]:
    """Launch `loop run` as a detached subprocess and return immediately."""
    executable = loop_executable()
    if shutil.which(executable) is None:
        raise ControlPlaneError(
            f"loop executable '{executable}' was not found on PATH"
            " (set AICP_LOOP_EXECUTABLE to override)"
        )

    cmd = _loop_command(req, executable)
    run_id = uuid.uuid4().hex
    now = utcnow()
    record: dict[str, Any] = {
        "run_id": run_id,
        "status": "running",
        "workspace_id": req.workspace_id,
        "project_path": req.project_path,
        "profile_name": req.profile_name,
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
        )
    except OSError as exc:
        raise ControlPlaneError(f"failed to launch loop run: {exc}") from exc

    record["_process"] = process
    _ACTIVE_LOOPS[run_id] = record
    _persist_start(run_id, req, cmd)
    threading.Thread(
        target=_reap, args=(run_id, process), daemon=True, name=f"loop-reap-{run_id}"
    ).start()
    return _snapshot(run_id)


def _load_run_from_db(run_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT run_id, workspace_id, project_path, profile_name, issue_ref,"
            " dry_run, status, evidence_uri, diff_summary, created_at, updated_at"
            " FROM loop_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["dry_run"] = bool(data.get("dry_run"))
    return data


def record_callback(req: LoopCallbackRequest) -> dict[str, Any]:
    """Apply a ForgeLoop completion callback: memory dict + one SQLite update."""
    record = _ACTIVE_LOOPS.get(req.run_id)
    if record is None:
        revived = _load_run_from_db(req.run_id)
        if revived is None:
            raise UnknownLoopRun(f"unknown run_id: {req.run_id}")
        record = revived
        _ACTIVE_LOOPS[req.run_id] = record

    now = utcnow()
    record["status"] = req.status
    record["evidence_uri"] = req.evidence_uri
    record["diff_summary"] = req.diff_summary
    if req.profile_name:
        record["profile_name"] = req.profile_name
    record["completed_at"] = now
    record["updated_at"] = now

    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO loop_runs (run_id, workspace_id, project_path, profile_name,"
            " issue_ref, dry_run, status, evidence_uri, diff_summary, created_at,"
            " updated_at, completed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(run_id) DO UPDATE SET"
            " status = excluded.status, evidence_uri = excluded.evidence_uri,"
            " diff_summary = excluded.diff_summary, profile_name = excluded.profile_name,"
            " updated_at = excluded.updated_at, completed_at = excluded.completed_at",
            (
                req.run_id,
                record.get("workspace_id") or "",
                record.get("project_path") or "",
                req.profile_name or record.get("profile_name"),
                record.get("issue_ref") or "",
                int(bool(record.get("dry_run"))),
                req.status,
                req.evidence_uri,
                req.diff_summary,
                record.get("created_at") or now,
                now,
                now,
            ),
        )
    return _snapshot(req.run_id)
