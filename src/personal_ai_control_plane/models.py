from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class RouteRequest(BaseModel):
    text: str = Field(min_length=1)


class ContextResolveRequest(BaseModel):
    text: str = Field(min_length=1)


BindingRole = Literal["primary", "related"]


class MulticaBindingInput(BaseModel):
    workspace: str = Field(min_length=1, description="Workspace slug/id/reference")
    project: str = Field(
        min_length=1, description="Project id/reference accepted by Multica CLI"
    )
    role: BindingRole = "related"
    profile: str | None = None


class OpenWebUIKnowledgeBindingInput(BaseModel):
    knowledge_id: str = Field(
        min_length=1, description="Existing Open WebUI Knowledge base id"
    )
    label: str | None = None
    role: BindingRole = "related"


class ContextCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9._-]+$", min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    multica_bindings: list[MulticaBindingInput] = Field(default_factory=list)
    openwebui_knowledge_bindings: list[OpenWebUIKnowledgeBindingInput] = Field(
        default_factory=list
    )


class ContextUpdate(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None


class ContextBriefRequest(BaseModel):
    text: str = Field(
        min_length=1,
        description="User's original natural-language question, including the context/project name",
    )


class LoopRunRequest(BaseModel):
    """Internal-only payload for POST /api/v1/loop/run."""

    workspace_id: str = Field(min_length=1)
    project_path: str = Field(min_length=1)
    profile_name: str | None = None
    # issue_ref is optional at the schema level so future target-less
    # (Discovery) runs validate; the spawn path still requires it today
    # because ForgeLoop has no target-less run mode yet (P2).
    issue_ref: str | None = None
    # Optional caller-supplied execution id; auto-generated as
    # exec-YYYYMMDD-XXXXXX when omitted.
    execution_id: str | None = Field(
        default=None,
        min_length=1,
        description="Adopt this execution_id instead of generating one",
    )
    dry_run: bool = True


class ExecutionRecord(BaseModel):
    """One WorkMesh-initiated execution, keyed by execution_id.

    Bridges WorkMesh -> ForgeLoop subprocess -> run.json -> webhook callback.
    """

    execution_id: str = Field(min_length=1)
    project_path: str = Field(min_length=1)
    profile_name: str | None = None
    target: str | None = None
    issue_ref: str | None = None
    status: str = "running"
    created_at: str | None = None
    updated_at: str | None = None


class LoopCallbackRequest(BaseModel):
    """ForgeLoop completion callback payload for POST /api/v1/loop/callback."""

    execution_id: str | None = Field(
        default=None, min_length=1, description="Preferred run correlation key"
    )
    run_id: str | None = Field(
        default=None,
        min_length=1,
        deprecated=True,
        description="Deprecated legacy key; use execution_id",
    )
    status: Literal["pass", "fail"]
    evidence_uri: str | None = None
    diff_summary: str | None = None
    profile_name: str | None = None

    @model_validator(mode="after")
    def _require_execution_or_run_id(self) -> LoopCallbackRequest:
        if not (self.execution_id or self.run_id):
            raise ValueError("execution_id or run_id is required")
        return self
