from personal_ai_control_plane.integrations.multica import MulticaClient
from personal_ai_control_plane.models import (
    ContextCreate,
    MulticaBindingInput,
    OpenWebUIKnowledgeBindingInput,
)
from personal_ai_control_plane.services.brief import context_brief
from personal_ai_control_plane.services.contexts import create_context


class FakeMultica(MulticaClient):
    def workspace_get(
        self, workspace_ref: str, *, profile: str | None = None
    ) -> dict[str, str]:
        return {"id": "ws-1", "slug": "media"}

    def issues(self, **kwargs):
        return [
            {
                "key": "MUL-1",
                "title": "Blocked issue",
                "status": "blocked",
                "priority": "urgent",
                "latest_run_status": "failed",
            },
            {
                "key": "MUL-2",
                "title": "Doing issue",
                "status": "in_progress",
                "priority": "high",
            },
            {
                "key": "MUL-3",
                "title": "Done issue",
                "status": "done",
                "priority": "low",
            },
        ]

    def project_resources(self, project_id, **kwargs):
        return [
            {
                "id": "r1",
                "resource_type": "github_repo",
                "resource_ref": {"url": "https://example.invalid/repo.git"},
            },
        ]

    def project_get(self, project_id, **kwargs):
        return {"id": project_id, "title": "Asset Center V4", "status": "in_progress"}


def test_context_brief_returns_live_work_and_kb_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    create_context(
        ContextCreate(
            id="asset-center",
            name="Asset Center",
            aliases=["媒资中心"],
            multica_bindings=[
                MulticaBindingInput(
                    workspace="media", project="project-1", role="primary"
                )
            ],
            openwebui_knowledge_bindings=[
                OpenWebUIKnowledgeBindingInput(knowledge_id="kb-1", label="Asset KB")
            ],
        )
    )

    result = context_brief("看看媒资中心现在什么情况", client=FakeMultica())
    assert result["matched"] is True
    assert result["context"]["context_id"] == "asset-center"
    assert result["work"]["open_issue_count"] == 2
    assert result["work"]["blocked_count"] == 1
    assert result["work"]["focus"][0]["run_state"] == "failed"
    assert result["knowledge_query_plan"]["tool"] == "query_knowledge_files"
    assert result["knowledge_query_plan"]["knowledge_ids"] == ["kb-1"]
    assert (
        result["project_resources"]["bindings"][0]["resources"][0]["resource_type"]
        == "github_repo"
    )
