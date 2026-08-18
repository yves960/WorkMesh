# WorkMesh Workflow 设计 v0.1

> **作者**：蔚（Director / 设计） · **日期**：2026-08-18 · **状态**：draft

## 🎯 目标

WorkMesh 从"薄中间桥"升级为"**薄中间桥 + Workflow Runner**"：
- 用户给自然语言意图 → WorkMesh 按 YAML 定义的工作流执行一系列 step → ForgeLoop 真正执行 → Multica 状态同步 → 用户收到通知
- 同一份 workflow 可被两个入口触发：Open WebUI 聊天 / Multica Issue 状态变 READY_FOR_AI
- **唯一启动 ForgeLoop 的入口是 WorkMesh**（不再让 Open WebUI/Multica 直调）

---

## 📦 Workflow YAML 定义格式

存放在 `workflows/<name>.yaml`。第一版：`workflows/java-ut-repair.yaml`。

```yaml
name: java-ut-repair
version: 1
description: 修复 Java 单元测试

# 触发条件
triggers:
  - on_user_message:
      keywords_any: ["UT", "test", "挂了", "fail"]
      min_intent_confidence: 0.5
  - on_issue_status_change:
      from: ["TODO"]
      to: ["READY_FOR_AI"]
      issue_type: ["test_failure"]

# Steps（按顺序执行；phase 1 MVP 全部顺序，phase 2+ 可声明 parallel）
steps:
  - id: knowledge
    type: knowledge
    inputs:
      query: "{{context.failure_summary}}"
      kb_id: "forgeloop-evidence"
      max_results: 5
    outputs: ["knowledge_results"]
    timeout_seconds: 30

  - id: context
    type: context
    inputs:
      include:
        - discovery.failure_summary
        - knowledge.knowledge_results
        - trigger.user_message
    outputs: ["context"]

  - id: discovery
    type: discovery
    inputs:
      workspace_root: "{{trigger.project_path}}"
      user_message: "UT 挂了，帮我修"
    outputs: ["recommended_target", "recommended_profile", "failing_tests"]

  - id: issue
    type: issue
    inputs:
      title_template: "修复 {{discovery.failing_tests[0]}}"
      description_template: "{{discovery.failure_summary}}"
      create_if_missing: true
      related_to_issue_id: "{{trigger.issue_id}}"  # 如果从 Multica 触发
    outputs: ["issue_id"]

  - id: approval
    type: approval
    inputs:
      plan_template: |
        Project: {{trigger.project_path}}
        Profile: {{discovery.recommended_profile}}
        Target:  {{discovery.recommended_target}}
        Reason:  {{discovery.failure_summary}}
      timeout_seconds: 300
      timeout_action: "auto_reject"  # 5min 超时自动拒绝（防工作流悬挂）
    outputs: ["approved", "approved_at"]

  - id: forgeloop
    type: forgeloop
    inputs:
      project_path: "{{trigger.project_path}}"
      profile: "{{discovery.recommended_profile}}"
      target: "{{discovery.recommended_target}}"
      issue_ref: "{{issue.issue_id}}"
    requires_approval: true   # 必须前序 approval step approved=true 才执行
    outputs: ["result", "execution_id"]

  - id: sync_multica
    type: sync_multica
    inputs:
      issue_id: "{{issue.issue_id}}"
      status_map:
        pass: "REVIEW"
        fail: "FAILED"
    outputs: ["multica_status"]

  - id: notify
    type: notify
    inputs:
      channel: "feishu"
      template: |
        ✅ Workflow `{{workflow.name}}` 完成
        Issue: {{issue.issue_id}}
        Result: {{forgeloop.result}}
        Multica 状态: {{sync_multica.multica_status}}
```

---

## 📊 Workflow Instance 状态机

```
PENDING → RUNNING → (PER_STEP: PENDING → RUNNING → DONE/FAILED)
  → COMPLETED (所有 step DONE)
  → FAILED (任一关键 step FAILED)
  → REJECTED (approval 拒绝或超时)
  → CANCELLED (手动取消)
```

**关键约束**：
- 一次 workflow 只能有一个 RUNNING instance per trigger 组合
- **幂等键** = `issue_id + workflow_name + (active execution_id OR trigger hash)`
- 同一幂等键重复触发 → 返现有 execution_id，不重启
- step FAILED 后续 step 跳过
- 整体 FAILED 但已经成功的 step 不回滚（idempotent 设计）

---

## 🛠️ Workflow Runner API

### 新增端点

| 端点 | 用途 |
|---|---|
| `POST /api/v1/workflow/start` | 启动一个新 workflow，body 含 workflow_name + trigger context |
| `GET /api/v1/workflow/{execution_id}` | 查询 workflow instance 状态 + 所有 step 输出 |
| `POST /api/v1/workflow/{execution_id}/approve` | 用户确认 approval step（body 含 decision + reason） |
| `POST /api/v1/workflow/{execution_id}/cancel` | 取消运行中的 workflow |
| `GET /api/v1/workflows` | 列出所有 YAML 定义（name + description + trigger summary） |
| `GET /api/v1/workflows/{name}` | 返回完整 YAML |

### Workflow instance 返回示例

``````json
{
  "execution_id": "wf-20260818-a1b2c3",
  "workflow_name": "java-ut-repair",
  "status": "RUNNING",
  "current_step": "forgeloop",
  "context": {
    "trigger": { "source": "open_webui", "user_message": "..." },
    "failure_summary": "..."
  },
  "step_results": [
    {"id": "knowledge", "status": "DONE", "outputs": {...}, "started_at": "...", "duration_ms": 120},
    {"id": "context",   "status": "DONE", "outputs": {...}, "duration_ms": 5},
    {"id": "discovery", "status": "DONE", "outputs": {...}, "duration_ms": 320},
    {"id": "issue",     "status": "DONE", "outputs": {...}, "duration_ms": 150},
    {"id": "approval",  "status": "WAITING", "outputs": null, "started_at": "..."}
  ],
  "started_at": "...",
  "updated_at": "..."
}
```

---

## 🧩 Step 类型注册表

| type | Phase 1（今天）| Phase 2（之后）|
|---|---|---|
| `knowledge` | stub（返 mock 数据）| 调 Open WebUI KB API |
| `context` | 真（拼字段）| 同 |
| `discovery` | **真**（调 Task 2 端点）| 同 |
| `issue` | stub | **真**（调 Multica create/update）|
| `approval` | stub（自动 approved=True）| **真**（等用户 /confirm，超时 auto_reject）|
| `forgeloop` | **真**（调 Task 3 /confirm 端点）| 同 |
| `sync_multica` | stub | **真**（update issue status）|
| `notify` | stub | **真**（飞书 / webhook / log）|

**stub 行为**：返 `{"stub": true, "would_have": "<action description>"}`——这样 Workflow Runner 能跑通端到端，phase 2 真做时只是换 stub → real。

---

## 🗃️ 持久化

新增 `workflow_runs` + `workflow_step_results` 表（Phase 1 SQLite，Phase 2 可迁 Postgres）。

```sql
CREATE TABLE workflow_runs (
  execution_id TEXT PRIMARY KEY,
  workflow_name TEXT NOT NULL,
  status TEXT NOT NULL,  -- PENDING/RUNNING/COMPLETED/FAILED/REJECTED/CANCELLED
  trigger_source TEXT NOT NULL,
  trigger_context TEXT NOT NULL,  -- JSON
  context TEXT,                    -- JSON
  started_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  completed_at INTEGER
);
CREATE TABLE workflow_step_results (
  execution_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  step_index INTEGER NOT NULL,
  status TEXT NOT NULL,  -- PENDING/RUNNING/DONE/FAILED/SKIPPED
  inputs TEXT,           -- JSON
  outputs TEXT,          -- JSON
  error TEXT,
  started_at INTEGER,
  updated_at INTEGER,
  duration_ms INTEGER,
  PRIMARY KEY (execution_id, step_id),
  FOREIGN KEY (execution_id) REFERENCES workflow_runs(execution_id)
);
CREATE INDEX idx_workflow_runs_status ON workflow_runs(status);
```

**幂等表**（新增）：
```sql
CREATE TABLE workflow_idempotency (
  idempotency_key TEXT PRIMARY KEY,  -- "{issue_id}|{workflow_name}|{execution_id_or_trigger_hash}"
  execution_id TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
```

---

## 📂 文件结构（Phase 1 MVP）

```
WorkMesh/
├── docs/
│   └── WORKFLOW_DESIGN.md  ← 本文件
├── workflows/
│   └── java-ut-repair.yaml ← 第一条 workflow
├── src/personal_ai_control_plane/
│   ├── workflow/
│   │   ├── __init__.py
│   │   ├── loader.py           ← YAML 加载 + schema 校验
│   │   ├── runner.py           ← 主执行循环
│   │   ├── state.py            ← Instance / StepResult dataclass + 状态机
│   │   ├── steps.py            ← Step registry + 8 个 step 工厂
│   │   └── idempotency.py      ← 幂等键管理
│   └── workflow_routes.py      ← 6 个端点
├── tests/
│   ├── test_workflow_loader.py
│   ├── test_workflow_runner.py
│   ├── test_workflow_idempotency.py
│   └── test_workflow_routes.py
```

---

## 🔒 安全 + 约束

- Workflow 启动时生成 `execution_id`（格式 `wf-YYYYMMDD-XXXXXX`，复用 Task 1 的 hex 生成逻辑）
- approval step **必须** user_id 显式（不接受 anonymous / system auto-approve）
- 每个 step 的 `timeout_seconds` **强制**（防工作流死等）
- 同一个 workflow + 同一 trigger hash 同时**只能有 1 个 RUNNING instance**（并发去重）
- FAILED step 不会自动 retry phase 1（人工触发重新启动 workflow）

---

## ✅ 验收（Phase 1）

1. `cd ~/projects/self/WorkMesh && uv run pytest` → 全绿
2. **手动 E2E**：
```bash
WORKFLOW=$(curl -X POST http://127.0.0.1:8765/api/v1/workflow/start \
  -H "Content-Type: application/json" \
  -d '{"workflow_name":"java-ut-repair","trigger":{"source":"open_webui","user_message":"UT 挂了，帮我修","project_path":"/Users/sy/projects/self/demo-java-ut"}}')

EXEC=$(echo $WORKFLOW | jq -r .execution_id)

# 查询状态
curl http://127.0.0.1:8765/api/v1/workflow/$EXEC | jq .
```
3. 验证：
- Workflow 走到 approval step 等 confirm
- curl `/approve` 后继续走
- 最终所有 step DONE → status=COMPLETED
- 幂等：同一 trigger 重发 → 返同一 execution_id

---

## 🚦 接下来

Phase 1（今天）→ Phase 2（Multica 真写 + Knowledge step + approval 真等 + notify 真发）

每 phase 完都推 GitHub（铁律 #3）。