from personal_ai_control_plane.integrations.multica import MulticaClient
from personal_ai_control_plane.models import ContextCreate, MulticaBindingInput
from personal_ai_control_plane.services.contexts import create_context
from personal_ai_control_plane.services.resources import context_resources


class FakeMultica(MulticaClient):
    def workspace_get(self, workspace_ref: str, *, profile: str | None = None):
        return {"id": "wid"}

    def project_resources(
        self,
        project_id: str,
        *,
        workspace_id: str | None = None,
        profile: str | None = None,
    ):
        return {
            "resources": [
                {
                    "id": "r1",
                    "resource_type": "github_repo",
                    "resource_ref": {"url": "https://example/repo.git", "ref": "main"},
                },
                {
                    "id": "r2",
                    "resource_type": "local_directory",
                    "resource_ref": {
                        "local_path": "/definitely/not/here",
                        "daemon_id": "d1",
                    },
                },
            ]
        }


def test_resources_are_live_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    create_context(
        ContextCreate(
            id="asset",
            name="Asset",
            multica_bindings=[
                MulticaBindingInput(workspace="ws", project="p1", role="primary")
            ],
        )
    )
    data = context_resources("asset", client=FakeMultica())
    resources = data["bindings"][0]["resources"]
    assert resources[0]["resource_type"] == "github_repo"
    assert resources[1]["resource_type"] == "local_directory"
    assert resources[1]["openwebui_computer_workspace_candidate"] is False
