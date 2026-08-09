# AGENTS.md

## Mission

This repository is a thin control plane between Open WebUI and Multica.

## Non-negotiable ownership boundaries

- Open WebUI owns chat, Knowledge/RAG, memory, Open WebUI Skills, and Computer workspace UX.
- Multica owns workspaces, projects, issues, tasks/runs, agents, execution skills, daemons/runtimes, and Project Resources.
- Control Plane owns only logical Context aliases, cross-system bindings, routing policy, and computed aggregation views.

## Rules

- Never add a vector DB, embedding pipeline, RAG implementation, local project/task store, agent registry, or skill registry.
- Never persist Multica Project Resources; read them live.
- Never copy Open WebUI Knowledge content; store only references when a Context needs a stable association.
- Writes to Multica remain disabled until an explicit write policy is designed.
- Prefer passthrough adapters and live references over sync jobs.
