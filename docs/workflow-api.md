# Workflow API

WorkMesh Workflow Runner（Phase 1）HTTP 契约。快捷入口：`python3 scripts/workflow.py --help`（CLI）；`python3 scripts/smoke_workflow.py`（脱离 pytest 的全流程冒烟）。

## 端点总览

| 方法 | 路径 | 请求体 | 说明 |
|------|------|--------|------|
| POST | `/api/v1/workflow/start` | `{workflow_name, trigger_context}` | 启动工作流（幂等：同 trigger 重放返回原 execution_id） |
| GET | `/api/v1/workflow/{execution_id}` | — | 实例/状态查询 |
| POST | `/api/v1/workflow/{execution_id}/approve` | `{decision: "approve"\|"reject", reason?}` | 审批（**仅 WAITING_FOR_APPROVAL 态**，否则 409） |
| POST | `/api/v1/workflow/{execution_id}/cancel` | `{reason}`（必填，缺了 422） | 取消（PENDING / RUNNING / WAITING_FOR_APPROVAL） |
| GET | `/api/v1/workflows` | — | 工作流定义列表 |
| GET | `/api/v1/workflows/{name}` | — | 单个定义 |

## 实例状态机（顶层 status，7 个取值）

```
PENDING ──▶ RUNNING ──▶ COMPLETED
              │ ▲
              │ └──（approve 后 resume）
              ▼
     WAITING_FOR_APPROVAL ──▶ REJECTED（reject）
              │
              ├──▶ RUNNING（approve：闸门后继续）
              └──▶ CANCELLED
RUNNING 也直达 FAILED / REJECTED / CANCELLED；四个终态（COMPLETED/FAILED/REJECTED/CANCELLED）不再转移。
```

注意：`DONE` 是**步级**状态（step_results[].status），不是实例状态——实例终态是上述四个。

审批闸门（2026-10-26）：runner 在 `type: approval` 的步、以及 `requires_approval: true` 的步之前停下并转入 WAITING_FOR_APPROVAL；未审批时危险步绝不进入 RUNNING、不产生副作用（如 run_id）。

## trigger_context 字段

| 字段 | 用途 | 匹配逻辑 |
|------|------|----------|
| `user_message` | `on_user_message` 触发 | 任一 keyword 命中（大小写不敏感） |
| `issue_id` | `on_issue_status_change` 触发 | 必须存在 |
| `issue_status` | 状态变更目标 | 需在 trigger 的 `to` 列表内（`from` 仅参考） |
| `project_path` | 供步骤模板 `{{trigger.project_path}}` 使用 | 不参与触发匹配 |

## 响应一致性

`start` 与 `GET /workflow/{execution_id}` 的基础形状相同（同一 `to_dict()` 序列化）。唯一差异：**幂等重放**时 `start` 响应额外携带 `"idempotent_reuse": true`，其余字段与首次启动一致，不会重启执行。

## 示例（真实捕获自 `scripts/smoke_workflow.py`）

### POST /api/v1/workflow/start

```json
{
  "workflow_name": "java-ut-repair",
  "trigger_context": {
    "user_message": "UT 挂了，帮我修",
    "project_path": "/path/to/repo"
  }
}
```

响应 `200`（节选，`step_results` 含全部 8 步）：

```json
{
  "execution_id": "wf-20261020-e921a2",
  "workflow_name": "java-ut-repair",
  "status": "RUNNING",
  "current_step": "discovery",
  "trigger_context": { "user_message": "UT 挂了，帮我修", "project_path": "/path/to/repo" },
  "context": {
    "trigger": { "user_message": "UT 挂了，帮我修", "project_path": "/path/to/repo" },
    "workflow": { "name": "java-ut-repair", "version": 2 }
  },
  "step_results": [
    { "step_id": "discovery", "step_index": 0, "status": "PENDING", "inputs": null, "outputs": null, "error": null, "duration_ms": null }
  ]
}
```

### trigger 不匹配

响应 `400`：

```json
{
  "detail": {
    "error": "trigger_context does not match any declared trigger",
    "triggers_summary": ["...该工作流声明的 trigger 摘要..."]
  }
}
```

### POST /api/v1/workflow/{execution_id}/approve

```json
{ "decision": "approve", "reason": "看过计划，没问题" }
```

`decision` 取值 `"approve" | "reject"`；**仅 WAITING_FOR_APPROVAL 态可调用**（实例停在审批/危险步闸门时），其它状态返回 `409` 且文案列出当前态与合法态。approve 后实例回 RUNNING 并从闸门后续跑；reject 转 REJECTED 且余步置 SKIPPED。

### step_results 字段

`status`: `PENDING | RUNNING | DONE | SKIPPED | FAILED`；每项含 `step_id` / `step_index` / `inputs` / `outputs` / `error` / `started_at` / `updated_at` / `duration_ms`。审批步被人工批准后 `inputs` 会补渲染留痕（计划可审阅），`outputs` 携带 `{decision, reason, approved_at}`。

## 静态依赖检查（loader）

`{{step_id.*}}` **前向引用**（引用后执行步骤的输出）在 load 阶段直接拒绝——线性执行模型下它会静默解析为 `None`/空串（`render_value` 行为），属于数据流 bug。保留域 `{{trigger.*}}` / `{{workflow.*}}` 不受顺序限制。

```bash
$ python3 scripts/workflow.py lint java-ut-repair
[lint] OK: java-ut-repair v2 — 8 steps, 2 triggers
  - discovery [discovery]
  - ...
$ python3 scripts/workflow.py lint bad
[lint] ERROR: steps[0].inputs: step 'a' 前向引用了后执行步骤 'b' 的输出（{{b.later}}）…
```
