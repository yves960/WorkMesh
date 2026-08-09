from personal_ai_control_plane.integrations.multica import MulticaClient
from personal_ai_control_plane.models import ContextCreate, MulticaBindingInput
from personal_ai_control_plane.services.contexts import create_context
from personal_ai_control_plane.services.today import build_today


class FakeMultica(MulticaClient):
    def workspace_get(
        self, workspace_ref: str, *, profile: str | None = None
    ) -> dict[str, str]:
        return {"id": f"ws-{workspace_ref}"}

    def issues(
        self,
        *,
        project: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 100,
        workspace_id: str | None = None,
        profile: str | None = None,
    ) -> list[dict[str, str]]:
        return [
            {
                "key": "MUL-9",
                "title": "Continue implementation",
                "status": "in_progress",
                "priority": "urgent",
                "due_date": "2026-08-09",
            },
            {
                "key": "MUL-8",
                "title": "Unblock dependency",
                "status": "blocked",
                "priority": "high",
                "due_date": "2026-08-10",
            },
            {
                "key": "MUL-7",
                "title": "Finished",
                "status": "done",
                "priority": "urgent",
            },
        ]


def test_today_prioritizes_live_blockers_and_excludes_closed_work(
    tmp_path,
    monkeypatch,
) -> None:
    # Given
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    create_context(
        ContextCreate(
            id="asset-center",
            name="Asset Center",
            multica_bindings=[
                MulticaBindingInput(
                    workspace="media",
                    project="asset-v4",
                    role="primary",
                )
            ],
        )
    )

    # When
    result = build_today(client=FakeMultica())

    # Then
    assert result["open_issues"] == 2
    assert result["blocked"] == 1
    assert result["in_progress"] == 1
    assert [item["issue"] for item in result["focus"]] == ["MUL-8", "MUL-9"]
