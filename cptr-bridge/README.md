# cptr-bridge

WorkMesh AICP ↔ cptr（Open WebUI Computer）的 OpenAPI tool server 桥接**骨架**。

把 AICP 已有的 6 个只读 `/agent/*` 工具（context-brief / contexts / today / issue 详情 / issue runs / run messages）以标准 OpenAPI tool server 形式暴露在 `127.0.0.1:8766`，供 cptr 注册。bridge 本身不含业务逻辑：`/openapi.json` 从 AICP 的 `/openwebui/openapi.json` 拉取并转换（AICP 不在线时回退到内置快照 `src/cptr_bridge/openapi.json`），工具调用用 httpx 原样代理到 AICP。不修改 AICP 主项目代码。

## 安装

```bash
cd ~/projects/self/WorkMesh/cptr-bridge
uv venv && uv pip install -e ".[dev]"
# 或: python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

## 启动

```bash
# 前置: AICP 已在 http://127.0.0.1:8765 运行（可用 AICP_URL 覆盖）
.venv/bin/python -m cptr_bridge.server          # 默认 127.0.0.1:8766
# 或装好后直接: cptr-bridge

# 验证
curl http://127.0.0.1:8766/openapi.json         # 应返回含 6 个 /agent/* 工具的 spec
curl http://127.0.0.1:8766/agent/today          # 代理到 AICP
```

环境变量：`AICP_URL`（默认 `http://127.0.0.1:8765`）、`CPTR_BRIDGE_PORT`（默认 `8766`）。

## 注册到 cptr

1. 保持 bridge 运行（`python -m cptr_bridge.server`）。
2. 在 cptr / Open WebUI 的 tool server 设置里添加 OpenAPI server，URL 填：
   `http://127.0.0.1:8766/openapi.json`
3. cptr 拉取 spec 后即可看到 6 个 `workspace_*` 只读工具。
4. 若 Open WebUI 与 bridge 不在同一网络命名空间，把 `127.0.0.1` 换成 bridge 所在机器的可达地址。

## 测试

```bash
.venv/bin/pytest tests/ -v
```

E2E 测试会启动一个 stub AICP + 真实 bridge 子进程，用 requests 验证 spec 与代理链路。

## 范围与边界（骨架）

- 只做透传代理，无鉴权/重试/缓存/改写逻辑（按约束保持骨架）。
- 上游必须是 AICP 的只读 agent spec；写操作端点不在暴露范围内。
- AICP 主项目零改动。
