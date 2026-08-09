from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from ..errors import MulticaCommandError, MulticaUnavailable


class MulticaClient:
    def __init__(self, executable: str = "multica", timeout_seconds: int = 30):
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def _base(
        self, workspace_id: str | None = None, profile: str | None = None
    ) -> list[str]:
        if not self.available():
            raise MulticaUnavailable(
                "multica executable was not found on PATH. Run `multica version` in CMD first."
            )
        cmd = [self.executable]
        if workspace_id:
            cmd += ["--workspace-id", workspace_id]
        if profile:
            cmd += ["--profile", profile]
        return cmd

    def _run(
        self,
        args: list[str],
        *,
        workspace_id: str | None = None,
        profile: str | None = None,
        json_output: bool = True,
    ) -> Any:
        cmd = self._base(workspace_id, profile) + args
        if json_output:
            cmd += ["--output", "json"]
        try:
            cp = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise MulticaCommandError(
                cmd, f"Multica command timed out after {self.timeout_seconds}s"
            ) from exc
        except OSError as exc:
            raise MulticaCommandError(cmd, str(exc)) from exc

        if cp.returncode != 0:
            message = (cp.stderr or cp.stdout or "Multica command failed").strip()
            raise MulticaCommandError(cmd, message, cp.returncode)

        output = (cp.stdout or "").strip()
        if not json_output:
            return output
        if not output:
            return None
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise MulticaCommandError(
                cmd,
                "Multica returned non-JSON output. Check whether this command/version supports `--output json`. "
                f"Raw output: {output[:500]}",
                cp.returncode,
            ) from exc

    def version(self) -> Any:
        return self._run(["version"])

    def auth_status(self) -> str:
        return self._run(["auth", "status"], json_output=False)

    def daemon_status(self) -> Any:
        return self._run(["daemon", "status"])

    def workspaces(self, *, profile: str | None = None) -> Any:
        # Workspace discovery is intentionally unscoped.
        return self._run(["workspace", "list"], profile=profile)

    def workspace_get(self, workspace_ref: str, *, profile: str | None = None) -> Any:
        # Multica accepts a workspace id/slug/short id for workspace get.
        return self._run(["workspace", "get", workspace_ref], profile=profile)

    def projects(
        self, *, workspace_id: str | None = None, profile: str | None = None
    ) -> Any:
        return self._run(
            ["project", "list"], workspace_id=workspace_id, profile=profile
        )

    def project_get(
        self,
        project_id: str,
        *,
        workspace_id: str | None = None,
        profile: str | None = None,
    ) -> Any:
        return self._run(
            ["project", "get", project_id], workspace_id=workspace_id, profile=profile
        )

    def project_resources(
        self,
        project_id: str,
        *,
        workspace_id: str | None = None,
        profile: str | None = None,
    ) -> Any:
        return self._run(
            ["project", "resource", "list", project_id],
            workspace_id=workspace_id,
            profile=profile,
        )

    def issues(
        self,
        *,
        project: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 100,
        workspace_id: str | None = None,
        profile: str | None = None,
    ) -> Any:
        args = ["issue", "list", "--limit", str(limit)]
        if project:
            args += ["--project", project]
        if status:
            args += ["--status", status]
        if priority:
            args += ["--priority", priority]
        return self._run(args, workspace_id=workspace_id, profile=profile)

    def issue_get(
        self,
        issue_id: str,
        *,
        workspace_id: str | None = None,
        profile: str | None = None,
    ) -> Any:
        return self._run(
            ["issue", "get", issue_id], workspace_id=workspace_id, profile=profile
        )

    def issue_search(
        self,
        query: str,
        *,
        include_closed: bool = False,
        limit: int = 50,
        workspace_id: str | None = None,
        profile: str | None = None,
    ) -> Any:
        args = ["issue", "search", query, "--limit", str(limit)]
        if include_closed:
            args.append("--include-closed")
        return self._run(args, workspace_id=workspace_id, profile=profile)

    def issue_runs(
        self,
        issue_id: str,
        *,
        workspace_id: str | None = None,
        profile: str | None = None,
    ) -> Any:
        return self._run(
            ["issue", "runs", issue_id], workspace_id=workspace_id, profile=profile
        )

    def issue_run_messages(
        self,
        task_id: str,
        *,
        workspace_id: str | None = None,
        profile: str | None = None,
    ) -> Any:
        return self._run(
            ["issue", "run-messages", task_id],
            workspace_id=workspace_id,
            profile=profile,
        )
