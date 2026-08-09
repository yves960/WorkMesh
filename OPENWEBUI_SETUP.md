# Open WebUI integration — v0.4

v0.4 exposes a **separate read-only OpenAPI spec** for normal Open WebUI chats.
Do not connect Open WebUI to the full `/openapi.json`, because that spec also contains Context administration endpoints.

## 1. Start Control Plane

```cmd
run-api.cmd
```

Verify:

```text
http://127.0.0.1:8765/health
http://127.0.0.1:8765/openwebui/openapi.json
```

## 2. Add the OpenAPI Tool Server in Open WebUI

For a personal/local setup, add a User Tool Server in Open WebUI Settings. The browser can call localhost directly.

Use:

```text
Type: OpenAPI
Server URL: http://127.0.0.1:8765
Spec path: openwebui/openapi.json
ID: aicp
```

After Verify, Open WebUI should discover these tools:

```text
aicp_workspace_context_brief
aicp_workspace_list_contexts
aicp_workspace_today
aicp_workspace_issue_detail
aicp_workspace_issue_runs
aicp_workspace_run_messages
```

## 3. Enable Open WebUI Builtin Knowledge tools

Use Native / Agentic function calling and keep the Knowledge Base builtin-tool category enabled.
The important built-in tool is:

```text
query_knowledge_files(query, knowledge_ids, count)
```

The Control Plane never retrieves or indexes Knowledge content. `workspace_context_brief` only returns the Knowledge IDs bound to the resolved Context. The model then calls Open WebUI's own `query_knowledge_files` tool if document evidence is needed.

## 4. Recommended model instruction

Add this to the model's system prompt/instructions:

```text
When the user asks about a configured work context, project status, progress, risks, current issues, or project resources, first call aicp_workspace_context_brief using the user's original question.

Treat the Multica data returned by that tool as the source of truth for current work status.

If the question needs design, specification, architecture, domain, historical, decision, or procedural knowledge, follow knowledge_query_plan. If it contains knowledge_ids, call Open WebUI's built-in query_knowledge_files with them. If no Knowledge is bound, use query_knowledge_bases/search_knowledge_bases with the Context name to discover the relevant KB, then query its files.

For a specific issue, use aicp_workspace_issue_detail. For execution history use aicp_workspace_issue_runs; if a failed task needs diagnosis, use aicp_workspace_run_messages for that task id.

All AICP agent tools are read-only. Never claim to have created, assigned, modified, retried, or deleted Multica/Open WebUI data.
```

## 5. Try these prompts

```text
看看 Asset Center 现在什么情况

Asset Center 当前有哪些风险？

MUL-128 现在什么状态？

MUL-128 上次为什么失败？

我今天优先处理什么？
```

Expected orchestration for a project-status question:

```text
User
  -> Open WebUI
  -> aicp_workspace_context_brief
       -> Context Resolver
       -> Multica Project / Issues / Resources
       -> returns bound Open WebUI Knowledge IDs
  -> (when needed) query_knowledge_files
  -> Open WebUI synthesizes the final answer
```
