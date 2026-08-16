# BRIDGE.md — WorkMesh × ForgeLoop × Open WebUI × Multica 集成桥接文档

> 对应代码基线：main @ `c909466`。所有行号以该 commit 为准。

## 1. 概述

WorkMesh（Personal AI Control Plane，下称 AICP）是 Open WebUI 与 Multica 之间的**薄中间桥**：对上，它把精选的只读 agent 工具（context-brief / today 等）通过 OpenAPI spec 暴露给 Open WebUI 的 cptr（Computer）桥接；对下，它把 Multica 的上下文与 ForgeLoop 的执行能力缝合起来——用内部端点以 detached 子进程方式启动 `loop run` 并接收完成回调，全程只绑 `127.0.0.1:8765`，不向外网暴露任何控制面。

## 2. 数据流（ASCII 框图）

W = WorkMesh (AICP) / A = cptr-bridge + Open WebUI / F = ForgeLoop（Multica 为数据源，M 标注）

```
 用户(在 Open WebUI 里说一句话)
   │
   ▼
 [A] Open WebUI agent ──调用──▶ [A] cptr-bridge (127.0.0.1:8766)
   │   工具: workspace_context_brief / workspace_today (只读)
   ▼
 [A] cptr-bridge ──httpx 原样代理──▶ [W] AICP /agent/* (127.0.0.1:8765)
   │
   ▼
 [W] AICP services/brief.py / today.py
     ├─ 读 Multica (M): contexts / issues / resources
     └─ 注入 active_loops 字段 (来自 loops._ACTIVE_LOOPS 内存字典)
   │
   │  (AICP 返回 JSON → bridge 透传 → Open WebUI agent 决定要跑一个 loop)
   ▼
 [W] 本地操作员/内部逻辑 ──POST──▶ [W] POST /api/v1/loop/run  (dry_run 默认 true)
   │   loops.start_loop(): Popen detached 子进程
   ▼
 [F] `loop run --target <issue_ref> --max-iterations N <profile>`
     (cwd=project_path; ForgeLoop 自行发现工作目录状态, 无 --workspace flag)
   │  ...ForgeLoop 跑编码 agent + 测试循环...
   ▼
 [F] hook-on-complete ──POST──▶ [W] POST /api/v1/loop/callback
   │   loops.record_callback(): 更新内存字典 + SQLite loop_runs 表
   ▼
 [W] GET /loop/status / 下次 brief/today 的 active_loops 字段
     → 回到第 3 步, Open WebUI agent 在下一轮看到结果
```

注意：`/api/v1/loop/run` 与 `/api/v1/loop/callback` **不在** curated `openwebui/openapi.json` spec 中（`loop_routes.py:3-5` 注释明示），Open WebUI agent 永远看不到它们；触发 loop 的是本机操作员/内部逻辑。

## 3. 三个内部端点（不暴露给 Open WebUI）

路由实现：`src/personal_ai_control_plane/loop_routes.py`；核心逻辑：`src/personal_ai_control_plane/loops.py`。

### 3.1 `POST /api/v1/loop/run`

启动 ForgeLoop detached 子进程，立即返回 run_id（`loop_routes.py:21-27`，`loops.py:135-180`）。

**请求 schema**（`models.py:58-65`，`LoopRunRequest`）：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `workspace_id` | `str` (min_length=1) | 必填 | 仅登记进 AICP 注册表/DB，不传给 ForgeLoop |
| `project_path` | `str` (min_length=1) | 必填 | 作为 Popen cwd，ForgeLoop 从工作目录发现项目 |
| `profile_name` | `str \| None` | `None` | ForgeLoop **必填位置参数**；为空时直接拒绝（`loops.py:67-72`） |
| `issue_ref` | `str` (min_length=1) | 必填 | 映射为 `--target`（ForgeLoop 无 `--issue` flag） |
| `dry_run` | `bool` | `True` | 见第 4 节 |

**响应**：run 快照 dict（`loops.py:147-158`），含 `run_id`、`status: "running"`、`workspace_id`、`project_path`、`profile_name`、`issue_ref`、`dry_run`、`command`（完整 argv）、`created_at`、`updated_at`。`_` 前缀键（如 `_process`）被剥离（`loops.py:85-86`）。

**副作用**：子进程以 `start_new_session=True` 脱离启动（`loops.py:160-169`），stdout/stderr/stdin 全部 DEVNULL；后台 daemon 线程 `_reap`（`loops.py:98-110`）在进程退出但无回调到达时把状态标为 `exited`/`crashed`；同时 INSERT SQLite `loop_runs` 表（`loops.py:113-132`，`db.py:48-63`）。

**鉴权**：无 token——信任本机调用者。服务仅绑 `127.0.0.1`（`run-api.sh:9-10`），非本机进程无法触达。

**调用方**：WorkMesh 内部（本地操作员 / 未来由内部逻辑触发）。Open WebUI 不可见。

### 3.2 `POST /api/v1/loop/callback`

记录 ForgeLoop 完成回调（`loop_routes.py:30-38`，`loops.py:199-244`）。

**请求 schema**（`models.py:68-75`，`LoopCallbackRequest`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | `str` (min_length=1) | 必须是 AICP 签发的 run_id |
| `status` | `"pass" \| "fail"` | 回调权威判定成败（优先于进程退出码，`loops.py:107-108`） |
| `evidence_uri` | `str \| None` | 证据链接 |
| `diff_summary` | `str \| None` | 变更摘要 |
| `profile_name` | `str \| None` | 可选回填 |

**响应**：更新后的 run 快照。**错误**：run_id 不在内存且不在 SQLite → `404 UnknownLoopRun`（`loop_routes.py:35-36`，`loops.py:183-207`，服务重启后可从 DB 复活记录）。

**副作用**：更新内存 dict + SQLite `loop_runs` UPSERT（`loops.py:218-243`），写 `completed_at`。

**鉴权**：同上，信任本机；ForgeLoop hook 以本机进程身份回调。

**调用方**：ForgeLoop hook-on-complete。

### 3.3 `GET /loop/status`

所有被跟踪 loop 的快照（`loop_routes.py:41-44`）。

**请求**：无参数。**响应**：`{"active_loops": [...快照列表...], "count": N}`（`loops.py:89-91`）。纯内存读，不碰子进程、不碰 DB。

**鉴权**：同上。**调用方**：WorkMesh 内部 / 本地操作员排障；`brief`/`today` 服务直接复用同一 `active_loops()` 函数注入字段（`services/brief.py:190,215`，`services/today.py:134`）。

## 4. dry_run 默认 True 的安全护栏

`LoopRunRequest.dry_run` 默认 `True`（`models.py:65`），SQLite `loop_runs.dry_run` 也默认 1（`db.py:54`）。设计动机：`loop run` 会真的启动编码 agent 改代码，默认必须在受控范围。

**真实映射**（`loops.py:61-65,73`，如实记录，勿误读）：

- ForgeLoop CLI **没有** `--dry-run` flag，也不存在 `--apply` flag（`loops.py:6-16` 记录了经 `test_loop_command_against_real_cli.py` 校验的真实 CLI 契约）。
- `dry_run=True` → `--max-iterations 1`：只跑**一轮有界迭代**，且这**不是 no-op**——编码 agent 仍会真实执行一次。
- 调用方要"真跑"（放开迭代上限），必须**显式传 `dry_run=false`**，此时映射为 `--max-iterations 5`（`FORGELOOP_MAX_ITERATIONS` 硬上限，`loops.py:37,64-65`——任何 profile 都无法突破该上限无限循环）。

即：等价于"--apply 才真跑"的语义由**请求体显式 opt-in** 实现，而非 CLI flag。

## 5. 升级 / 部署 / 测试

```bash
cd ~/projects/self/WorkMesh

# 健康检查（cli.py:53-54 定义）
uv run aicp doctor

# 全量测试（32 个，含 loop 端点 + brief/today 的 active_loops 覆盖）
uv run pytest tests/

# 起 AICP 主服务（仅 127.0.0.1:8765，run-api.sh）
./run-api.sh

# 起 cptr-bridge（骨架，127.0.0.1:8766；前置 AICP 已在线）
cd cptr-bridge && ./.venv/bin/python -m cptr_bridge.server
# 验证: curl http://127.0.0.1:8766/openapi.json  (应含 6 个 /agent/* 只读工具)
```

环境变量：`AICP_LOOP_EXECUTABLE`（覆盖 `loop` 可执行名，`loops.py:44-45`）、`AICP_URL` / `CPTR_BRIDGE_PORT`（bridge 上游与端口）。

## 6. 已知限制 / 下一步

1. **callback → oikb 同步未接线**：回调结果尚未同步进 oikb 知识库，依赖 oikb token（等 kimi 恢复后接入）。
2. **node companion app 未装**：需用户手动安装/授权，代码侧无法代做。
3. **callback run_id 关联断裂**：`record_callback` 按 `run_id` 精确匹配（`loops.py:201-207`），但 ForgeLoop 当前不支持按 run 透传 AICP 签发的 run_id，hook-on-complete 无法可靠带上正确 id——待 ForgeLoop 侧支持按 run 传 AICP run_id 后闭合链路。在此之前 callback 可能命中 404（DB 复活也救不了不存在的 id）。
