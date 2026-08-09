from __future__ import annotations

from typing import TypeAlias

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)


def build_spec() -> dict[str, JsonValue]:
    """A deliberately small, read-only OpenAPI surface for Open WebUI agents.

    The full FastAPI app still exposes admin/configuration endpoints at /docs,
    but Open WebUI should be configured against this filtered spec so normal
    chats cannot mutate Context bindings.
    """
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Personal AI Control Plane - Open WebUI Agent Tools",
            "version": "0.4.0",
            "description": "Read-only context, Multica work status, and routing tools. Configuration is intentionally excluded.",
        },
        "paths": {
            "/agent/context-brief": {
                "post": {
                    "operationId": "workspace_context_brief",
                    "summary": "Get a live work brief for a named context",
                    "description": (
                        "PRIMARY TOOL for questions about a project/context's status, progress, risks, issues, project resources, "
                        "or current work. Pass the user's original question unchanged. The result contains live Multica work data "
                        "plus Open WebUI Knowledge IDs. If knowledge_query_plan.bound is true and the question needs design, "
                        "specification, domain, history, or decision evidence, then call Open WebUI's built-in query_knowledge_files "
                        "with knowledge_query_plan.query and knowledge_query_plan.knowledge_ids before answering."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["text"],
                                    "properties": {
                                        "text": {
                                            "type": "string",
                                            "description": "The user's original natural-language question",
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Resolved context brief"}},
                }
            },
            "/agent/contexts": {
                "get": {
                    "operationId": "workspace_list_contexts",
                    "summary": "List configured logical work contexts",
                    "description": "Use when a context name is missing, ambiguous, or the user asks what work contexts are configured.",
                    "responses": {"200": {"description": "Configured contexts"}},
                }
            },
            "/agent/today": {
                "get": {
                    "operationId": "workspace_today",
                    "summary": "Get today's live work focus from Multica",
                    "description": "Use when the user asks what to work on today, current priorities, blockers, or active work across contexts.",
                    "responses": {"200": {"description": "Today summary"}},
                }
            },
            "/agent/issues/{issue_id}": {
                "get": {
                    "operationId": "workspace_issue_detail",
                    "summary": "Read one Multica issue",
                    "description": "Use for a specific issue key/id. If context_id is known, pass it so the correct Multica workspace is selected.",
                    "parameters": [
                        {
                            "name": "issue_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "context_id",
                            "in": "query",
                            "required": False,
                            "schema": {"type": ["string", "null"]},
                        },
                    ],
                    "responses": {"200": {"description": "Issue detail"}},
                }
            },
            "/agent/issues/{issue_id}/runs": {
                "get": {
                    "operationId": "workspace_issue_runs",
                    "summary": "Read agent runs for a Multica issue",
                    "description": "Use when the user asks about run history, retries, whether an agent ran, or why work may have failed. Follow with workspace_run_messages for a failed task when needed.",
                    "parameters": [
                        {
                            "name": "issue_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "context_id",
                            "in": "query",
                            "required": False,
                            "schema": {"type": ["string", "null"]},
                        },
                    ],
                    "responses": {"200": {"description": "Issue runs"}},
                }
            },
            "/agent/runs/{task_id}/messages": {
                "get": {
                    "operationId": "workspace_run_messages",
                    "summary": "Read messages/logical transcript for one Multica task run",
                    "description": "Use after workspace_issue_runs when diagnosing a specific failed or incomplete task run.",
                    "parameters": [
                        {
                            "name": "task_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "context_id",
                            "in": "query",
                            "required": False,
                            "schema": {"type": ["string", "null"]},
                        },
                    ],
                    "responses": {"200": {"description": "Run messages"}},
                }
            },
        },
    }
