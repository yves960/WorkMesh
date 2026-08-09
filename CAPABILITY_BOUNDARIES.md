# Capability Boundaries

The control plane follows one rule: **reference existing systems of record; never duplicate them**.

| Capability | Owner | Control Plane behavior |
|---|---|---|
| Chat / user entry | Open WebUI | Connect as an OpenAPI Tool Server |
| Knowledge / RAG | Open WebUI | Store only Knowledge IDs as bindings |
| Memory | Open WebUI | No local memory implementation |
| Conversational Skills | Open WebUI | No duplicate skill registry |
| Computer workspace / files / terminal / Git | Open WebUI Computer | Use Multica local_directory resources as workspace candidates only |
| Multica Workspace / Project | Multica | Store only references |
| Issues | Multica | Read live; future writes go back to Multica |
| Agent Tasks / Runs | Multica | Read live |
| Agents / runtime / daemon | Multica | Never recreate |
| Agent execution Skills | Multica / repository-native skills | Never recreate |
| Project Resources | Multica | Read live with `multica project resource list` |
| Context aliases / cross-system mapping | Control Plane | Own |
| Intent routing | Control Plane | Own |
| Today aggregation | Control Plane | Compute from live systems; do not persist work state |

## Context data model

```text
Context
├── id / name / aliases             [owned by Control Plane]
├── Multica Bindings[]              [references only]
│   └── Workspace + Project
│       └── Project Resources       [read live from Multica]
└── Open WebUI Knowledge Bindings[] [Knowledge IDs only]
```

The control plane deliberately has no local root registry, vector store, knowledge source registry, task table, project table, agent table, or skill registry.

## v0.4 Open WebUI agent boundary

Open WebUI normal chats MUST connect to `/openwebui/openapi.json`, not the full FastAPI `/openapi.json`.
The curated spec is read-only and intentionally excludes Context CRUD/binding administration.

Knowledge retrieval remains an Open WebUI built-in-tool action (`query_knowledge_files`). Control Plane may return Knowledge IDs as routing metadata but must not proxy/reimplement RAG as its normal path.
