from __future__ import annotations

import json
from typing import Annotated

import typer

from .db import init_db
from .models import (
    BindingRole,
    ContextCreate,
    MulticaBindingInput,
    OpenWebUIKnowledgeBindingInput,
)
from .services.brief import context_brief
from .services.contexts import (
    add_multica_binding,
    add_openwebui_knowledge_binding,
    create_context,
    delete_context,
    get_context,
    list_contexts,
    resolve_context,
)
from .services.doctor import doctor
from .services.migrate_v03 import migrate_v03_legacy_tables
from .services.multica_work import (
    context_issues,
    get_issue,
    get_issue_runs,
    get_run_messages,
    list_projects,
    list_workspaces,
)
from .services.resources import context_resources, effective_context
from .services.router import route_intent
from .services.today import build_today

app = typer.Typer(no_args_is_help=True, help="Personal AI Control Plane")
context_app = typer.Typer(no_args_is_help=True, help="Manage logical Context bindings")
app.add_typer(context_app, name="context")


def echo(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


@app.command("init-db")
def init_db_cmd():
    echo({"database": str(init_db())})


@app.command("doctor")
def doctor_cmd():
    echo(doctor())


@app.command("multica-workspaces")
def multica_workspaces(profile: str | None = typer.Option(None)):
    echo(list_workspaces(profile=profile))


@app.command("multica-projects")
def multica_projects(
    workspace: str = typer.Option(..., help="Workspace id/slug"),
    profile: str | None = typer.Option(None),
):
    echo(list_projects(workspace, profile=profile))


@app.command("today")
def today():
    echo(build_today())


@app.command("route")
def route(text: str):
    echo(route_intent(text))


@app.command("brief")
def brief(text: str):
    """Build the same live read-only context brief exposed to Open WebUI."""
    echo(context_brief(text))


@app.command("issue")
def issue(
    issue_id: str,
    context: str | None = typer.Option(None),
    binding_id: int | None = typer.Option(None),
):
    echo(get_issue(issue_id, context_id=context, binding_id=binding_id))


@app.command("issue-runs")
def issue_runs(
    issue_id: str,
    context: str | None = typer.Option(None),
    binding_id: int | None = typer.Option(None),
):
    echo(get_issue_runs(issue_id, context_id=context, binding_id=binding_id))


@app.command("run-messages")
def run_messages(
    task_id: str,
    context: str | None = typer.Option(None),
    binding_id: int | None = typer.Option(None),
):
    echo(get_run_messages(task_id, context_id=context, binding_id=binding_id))


@app.command("migrate-v03")
def migrate_v03():
    echo(migrate_v03_legacy_tables())


@context_app.command("list")
def context_list():
    echo({"contexts": list_contexts()})


@context_app.command("add")
def context_add(
    context_id: str, name: str = typer.Option(...), aliases: str = typer.Option("")
):
    alias_list = [x.strip() for x in aliases.split(",") if x.strip()]
    echo(create_context(ContextCreate(id=context_id, name=name, aliases=alias_list)))


@context_app.command("show")
def context_show(context_id: str):
    echo(get_context(context_id))


@context_app.command("resolve")
def context_resolve(text: str):
    echo(resolve_context(text))


@context_app.command("delete")
def context_delete(context_id: str):
    echo(delete_context(context_id))


@context_app.command("bind-multica")
def bind_multica(
    context_id: str,
    workspace: str = typer.Option(...),
    project: str = typer.Option(...),
    role: Annotated[BindingRole, typer.Option()] = "related",
    profile: str | None = typer.Option(None),
):
    echo(
        add_multica_binding(
            context_id,
            MulticaBindingInput(
                workspace=workspace, project=project, role=role, profile=profile
            ),
        )
    )


@context_app.command("bind-knowledge")
def bind_knowledge(
    context_id: str,
    knowledge_id: str = typer.Option(...),
    label: str | None = typer.Option(None),
    role: Annotated[BindingRole, typer.Option()] = "related",
):
    echo(
        add_openwebui_knowledge_binding(
            context_id,
            OpenWebUIKnowledgeBindingInput(
                knowledge_id=knowledge_id, label=label, role=role
            ),
        )
    )


@context_app.command("resources")
def resources(context_id: str):
    echo(context_resources(context_id))


@context_app.command("effective")
def effective(context_id: str, verify_openwebui: bool = typer.Option(False)):
    echo(effective_context(context_id, verify_openwebui=verify_openwebui))


@context_app.command("issues")
def issues(context_id: str, limit: int = typer.Option(100)):
    echo(context_issues(context_id, limit=limit))
