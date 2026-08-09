from personal_ai_control_plane.errors import MulticaCommandError
from personal_ai_control_plane.integrations.multica import MulticaClient
from personal_ai_control_plane.models import ContextCreate, MulticaBindingInput
from personal_ai_control_plane.services.contexts import create_context
from personal_ai_control_plane.services.multica_work import (
    get_issue,
    get_issue_runs,
    get_run_messages,
)


class FakeMultica(MulticaClient):
    def workspace_get(
        self, workspace_ref: str, *, profile: str | None = None
    ) -> dict[str, str]:
        return {"id": f"ws-{workspace_ref}"}

    def issue_get(
        self,
        issue_id: str,
        *,
        workspace_id: str | None = None,
        profile: str | None = None,
    ) -> dict[str, str]:
        if workspace_id == "ws-team-b":
            return {"key": issue_id, "status": "in_progress"}
        raise MulticaCommandError(["multica", "issue", "get", issue_id], "not found", 1)

    def issue_runs(
        self,
        issue_id: str,
        *,
        workspace_id: str | None = None,
        profile: str | None = None,
    ) -> list[dict[str, str]]:
        if workspace_id == "ws-team-b":
            return [{"id": "run-2", "status": "failed", "issue": issue_id}]
        raise MulticaCommandError(
            ["multica", "issue", "runs", issue_id], "not found", 1
        )

    def issue_run_messages(
        self,
        task_id: str,
        *,
        workspace_id: str | None = None,
        profile: str | None = None,
    ) -> list[dict[str, str]]:
        if workspace_id == "ws-team-b":
            return [{"role": "assistant", "content": f"{task_id} failed"}]
        raise MulticaCommandError(
            ["multica", "issue", "run-messages", task_id],
            "not found",
            1,
        )


def _configure_two_contexts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    for context_id, workspace in (("alpha", "team-a"), ("beta", "team-b")):
        create_context(
            ContextCreate(
                id=context_id,
                name=context_id.title(),
                multica_bindings=[
                    MulticaBindingInput(
                        workspace=workspace,
                        project=f"project-{context_id}",
                        role="primary",
                    )
                ],
            )
        )


def test_issue_detail_searches_configured_contexts_when_context_is_omitted(
    tmp_path,
    monkeypatch,
) -> None:
    # Given
    _configure_two_contexts(tmp_path, monkeypatch)

    # When
    result = get_issue("MUL-128", client=FakeMultica())

    # Then
    assert result["context"]["context_id"] == "beta"
    assert result["issue"]["key"] == "MUL-128"


def test_issue_runs_search_configured_contexts_when_context_is_omitted(
    tmp_path,
    monkeypatch,
) -> None:
    # Given
    _configure_two_contexts(tmp_path, monkeypatch)

    # When
    result = get_issue_runs("MUL-128", client=FakeMultica())

    # Then
    assert result["context"]["context_id"] == "beta"
    assert result["runs"][0]["status"] == "failed"


def test_run_messages_search_configured_contexts_when_context_is_omitted(
    tmp_path,
    monkeypatch,
) -> None:
    # Given
    _configure_two_contexts(tmp_path, monkeypatch)

    # When
    result = get_run_messages("run-2", client=FakeMultica())

    # Then
    assert result["context"]["context_id"] == "beta"
    assert result["messages"][0]["content"] == "run-2 failed"
