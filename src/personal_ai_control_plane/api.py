from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .admin import admin_page
from .agent_routes import router as agent_router
from .discovery_routes import router as discovery_router
from .execution_routes import router as execution_router
from .http_errors import CONTROL_ERRORS, raise_http
from .loop_routes import router as loop_router
from .models import (
    ContextCreate,
    ContextResolveRequest,
    ContextUpdate,
    MulticaBindingInput,
    OpenWebUIKnowledgeBindingInput,
    RouteRequest,
)
from .openwebui_agent_spec import build_spec
from .services.contexts import (
    add_multica_binding,
    add_openwebui_knowledge_binding,
    create_context,
    delete_context,
    delete_multica_binding,
    delete_openwebui_knowledge_binding,
    get_context,
    list_contexts,
    resolve_context,
    update_context,
)
from .services.doctor import doctor
from .services.migrate_v03 import migrate_v03_legacy_tables
from .services.multica_work import (
    context_issues,
    get_issue,
    get_issue_runs,
    list_projects,
    list_workspaces,
)
from .services.resources import context_resources, effective_context
from .services.router import route_intent
from .services.today import build_today
from .workflow_routes import router as workflow_router

app = FastAPI(
    title="Personal AI Control Plane",
    version="0.4.0",
    description="Thin context-binding and routing layer between Open WebUI and Multica.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(agent_router)
app.include_router(loop_router)
app.include_router(discovery_router)
app.include_router(execution_router)
app.include_router(workflow_router)


@app.get("/", include_in_schema=False)
def root():
    return {
        "name": "Personal AI Control Plane",
        "version": "0.4.0",
        "admin": "/admin",
        "docs": "/docs",
    }


@app.get("/admin", include_in_schema=False)
def admin():
    return admin_page()


@app.get("/health", operation_id="control_health")
def health():
    return {
        "status": "ok",
        "version": "0.4.0",
        "multica_mode": "read_only",
        "principle": "bind_and_route_do_not_duplicate",
    }


@app.get("/doctor", operation_id="control_doctor")
def control_doctor():
    return doctor()


@app.get("/contexts", operation_id="control_list_contexts")
def control_list_contexts():
    return {"contexts": list_contexts()}


@app.post("/contexts", operation_id="control_create_context")
def control_create_context(payload: ContextCreate):
    try:
        return create_context(payload)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.patch("/contexts/{context_id}", operation_id="control_update_context")
def control_update_context(context_id: str, payload: ContextUpdate):
    try:
        return update_context(context_id, payload)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.delete("/contexts/{context_id}", operation_id="control_delete_context")
def control_delete_context(context_id: str):
    try:
        return delete_context(context_id)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.post("/contexts/resolve", operation_id="control_resolve_context")
def control_resolve_context(payload: ContextResolveRequest):
    try:
        return resolve_context(payload.text)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.get("/contexts/{context_id}", operation_id="control_get_context")
def control_get_context(context_id: str):
    try:
        return get_context(context_id)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.get(
    "/contexts/{context_id}/effective", operation_id="control_get_effective_context"
)
def control_get_effective_context(context_id: str, verify_openwebui: bool = False):
    """Resolve live Multica Project Resources and Open WebUI Knowledge references for one logical context."""
    try:
        return effective_context(context_id, verify_openwebui=verify_openwebui)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.post(
    "/contexts/{context_id}/multica-bindings",
    operation_id="control_add_multica_binding",
)
def control_add_multica_binding(context_id: str, payload: MulticaBindingInput):
    try:
        return add_multica_binding(context_id, payload)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.delete(
    "/contexts/{context_id}/multica-bindings/{binding_id}",
    operation_id="control_delete_multica_binding",
)
def control_delete_multica_binding(context_id: str, binding_id: int):
    try:
        return delete_multica_binding(context_id, binding_id)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.get(
    "/contexts/{context_id}/resources", operation_id="control_get_context_resources"
)
def control_get_context_resources(context_id: str):
    """Read Project Resources live from Multica. Control Plane does not persist these resources."""
    try:
        return context_resources(context_id)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.post(
    "/contexts/{context_id}/openwebui-knowledge",
    operation_id="control_bind_openwebui_knowledge",
)
def control_bind_openwebui_knowledge(
    context_id: str, payload: OpenWebUIKnowledgeBindingInput
):
    """Bind an existing Open WebUI Knowledge base by id; this does not copy/index its content."""
    try:
        return add_openwebui_knowledge_binding(context_id, payload)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.delete(
    "/contexts/{context_id}/openwebui-knowledge/{binding_id}",
    operation_id="control_unbind_openwebui_knowledge",
)
def control_unbind_openwebui_knowledge(context_id: str, binding_id: int):
    try:
        return delete_openwebui_knowledge_binding(context_id, binding_id)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.get("/multica/workspaces", operation_id="control_list_multica_workspaces")
def control_list_multica_workspaces(profile: str | None = None):
    try:
        return {"workspaces": list_workspaces(profile=profile)}
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.get("/multica/projects", operation_id="control_list_multica_projects")
def control_list_multica_projects(
    workspace: str | None = None, profile: str | None = None
):
    try:
        return list_projects(workspace, profile=profile)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.get("/contexts/{context_id}/issues", operation_id="control_get_context_issues")
def control_get_context_issues(
    context_id: str,
    binding_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    try:
        return context_issues(context_id, binding_id=binding_id, limit=limit)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.get("/multica/issues/{issue_id}", operation_id="control_get_issue")
def control_get_issue(
    issue_id: str, context_id: str | None = None, binding_id: int | None = None
):
    try:
        return get_issue(issue_id, context_id=context_id, binding_id=binding_id)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.get("/multica/issues/{issue_id}/runs", operation_id="control_get_issue_runs")
def control_get_issue_runs(
    issue_id: str, context_id: str | None = None, binding_id: int | None = None
):
    try:
        return get_issue_runs(issue_id, context_id=context_id, binding_id=binding_id)
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.get("/today", operation_id="control_today")
def control_today():
    try:
        return build_today()
    except CONTROL_ERRORS as exc:
        raise_http(exc)


@app.post("/route", operation_id="control_route_intent")
def control_route_intent(payload: RouteRequest):
    return route_intent(payload.text)


@app.get("/openwebui/openapi.json", include_in_schema=False)
def openwebui_openapi_spec():
    return JSONResponse(build_spec())


@app.post("/migration/v03", operation_id="control_migrate_v03")
def control_migrate_v03():
    return migrate_v03_legacy_tables()
