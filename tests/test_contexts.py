from personal_ai_control_plane.models import (
    ContextCreate,
    MulticaBindingInput,
    OpenWebUIKnowledgeBindingInput,
)
from personal_ai_control_plane.services.contexts import create_context


def test_context_only_stores_bindings(tmp_path, monkeypatch):
    monkeypatch.setenv("AICP_ROOT", str(tmp_path))
    ctx = create_context(
        ContextCreate(
            id="asset",
            name="Asset",
            aliases=["媒资"],
            multica_bindings=[
                MulticaBindingInput(workspace="ws", project="p1", role="primary")
            ],
            openwebui_knowledge_bindings=[
                OpenWebUIKnowledgeBindingInput(knowledge_id="kb1", label="Domain")
            ],
        )
    )
    assert ctx["multica_bindings"][0]["project"] == "p1"
    assert ctx["openwebui_knowledge_bindings"][0]["knowledge_id"] == "kb1"
    assert "local_roots" not in ctx
    assert "knowledge_sources" not in ctx
