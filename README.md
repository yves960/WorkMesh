# Personal AI Control Plane v0.4

[中文使用指南](README.zh-CN.md)

v0.4 is the first version intended to be used **from Open WebUI as the normal conversational entry point**.

The architecture stays deliberately thin:

```text
                         Open WebUI
          Chat + Native Tools + Knowledge/RAG
                              |
                  read-only OpenAPI tools
                              |
                              v
                Personal AI Control Plane
             Context resolution + aggregation
                   /                    \
                  v                      v
              Multica              Binding refs
      Project / Issue / Run       to Open WebUI KBs
      Project Resources
```

## Ownership rule

- Open WebUI owns Chat, Knowledge/RAG, Memory, built-in Knowledge tools, and Computer Workspace.
- Multica owns Workspace, Project, Issue, Task/Run, Agent, Skills, Daemon, and Project Resources.
- Control Plane owns only logical Context names/aliases, cross-system bindings, routing, and dynamic aggregation.

See `CAPABILITY_BOUNDARIES.md`.

## What v0.4 adds

- `workspace_context_brief`: one read-only tool for natural-language project/context status questions.
- Live Multica Project metadata + Issues + Project Resources in the brief.
- Open WebUI Knowledge IDs returned as a **query plan**, not queried by Control Plane.
- Multica run-message passthrough for failed-run diagnosis.
- A curated Open WebUI-only OpenAPI spec at `/openwebui/openapi.json`.
- Admin/configuration endpoints are intentionally excluded from the Open WebUI agent spec.

## Windows install

```cmd
cd /d D:\a-v04
install.cmd
run-api.cmd
```

Open:

```text
Admin:            http://127.0.0.1:8765/admin
Full API docs:    http://127.0.0.1:8765/docs
Agent OpenAPI:    http://127.0.0.1:8765/openwebui/openapi.json
```

## macOS install

Install a Python version compatible with this project's `requires-python` declaration. The installer discovers and uses that system interpreter; it does not pin or download a specific Python release.

```bash
brew install uv
chmod +x install.sh run-api.sh
./install.sh
./run-api.sh
```

See `IMPLEMENTATION_GUIDE.md` for Linux Open WebUI deployment, macOS/Windows setup, Multica-managed OpenCode integration, Context configuration, and the seven Phase 1 acceptance scenarios.

## Existing v0.3.1 database

Copy your existing database into the new version before starting:

```text
v0.3.1\data\control-plane.db
      ->
v0.4\data\control-plane.db
```

The schema is compatible; no migration is required from v0.3.1 to v0.4.

## Test before Open WebUI

```cmd
.venv\Scripts\aicp.exe doctor
.venv\Scripts\aicp.exe brief "看看 Asset Center 现在什么情况"
.venv\Scripts\aicp.exe today
```

`brief` should resolve the logical Context, query live Multica data, list Project Resources, and return any bound Open WebUI Knowledge IDs.

## Connect Open WebUI

Use a local OpenAPI Tool Server:

```text
Server URL: http://127.0.0.1:8765
Spec path:  openwebui/openapi.json
ID:         aicp
```

See `OPENWEBUI_SETUP.md` for the recommended model instruction and test prompts.

## Important: Knowledge stays in Open WebUI

When `workspace_context_brief` returns:

```json
{
  "knowledge_query_plan": {
    "bound": true,
    "tool": "query_knowledge_files",
    "query": "看看 Asset Center 现在什么情况",
    "knowledge_ids": ["kb-id-1"]
  }
}
```

Open WebUI itself should call its built-in `query_knowledge_files` tool. The Control Plane does not implement RAG and does not copy Knowledge content.
