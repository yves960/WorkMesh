# Personal AI Workspace 实施指南

本文覆盖第一阶段：Linux Open WebUI、Windows/macOS 本地 AICP、Multica/OpenCode 执行链路，以及七个只读验收场景。

## 1. 边界

- Open WebUI 是 Chat、Knowledge/RAG、Memory、用户与权限的事实源。
- Multica 是 Workspace、Project、Issue、Agent、Task/Run 和执行历史的事实源。
- OpenCode 是由 Multica Agent/Daemon 调用的 Coding Agent，不由 AICP 注册或直接调度。
- AICP 只保存 Context、别名、Multica 引用和 Open WebUI Knowledge ID；Project Resources、Issue、Run 和 Knowledge 内容均实时读取或交回所属系统处理。
- 第一阶段所有 Open WebUI Agent Tool 都是只读的。

## 2. Linux 安装 Open WebUI

以下命令需要 root 权限。先安装与当前 Open WebUI 版本兼容的 Python；命令直接使用服务器现有的 `python3`，不固定小版本：

```bash
useradd --system --home /opt/open-webui --shell /usr/sbin/nologin openwebui
install -d -o openwebui -g openwebui /opt/open-webui /data/open-webui
PYTHON_BIN=$(command -v python3)
"$PYTHON_BIN" --version
"$PYTHON_BIN" -m venv /opt/open-webui/.venv
/opt/open-webui/.venv/bin/pip install --upgrade pip open-webui
chown -R openwebui:openwebui /opt/open-webui /data/open-webui
```

复制仓库中的 systemd 单元：

```bash
install -m 0644 deploy/open-webui/open-webui.service /etc/systemd/system/open-webui.service
systemctl daemon-reload
systemctl enable --now open-webui
systemctl status open-webui
```

日志：

```bash
journalctl -u open-webui -f
```

浏览器访问 `http://<linux-server>:8080`。生产环境应在 Open WebUI 前配置 HTTPS 反向代理；AICP 仍只监听个人电脑的 `127.0.0.1`。

## 3. macOS 本地安装 AICP

前置条件：

```bash
brew install uv
python3 --version
```

本机 Python 只需满足 `pyproject.toml` 中的最低兼容范围。`install.sh` 会发现实际安装的系统解释器并据此创建 `.venv`，不会下载或锁定某个 Python 小版本。

安装和启动：

```bash
chmod +x install.sh run-api.sh
./install.sh
./run-api.sh
```

验证：

```bash
curl http://127.0.0.1:8765/health
uv run aicp doctor
open http://127.0.0.1:8765/admin
```

数据库位于 `data/control-plane.db`。AICP 默认仅监听回环地址，不提供多租户、认证或 Credential Vault。

## 4. Windows 本地安装 AICP

先安装 `uv` 和满足 `pyproject.toml` 兼容范围的任一 Python，然后在项目目录执行：

```cmd
install.cmd
run-api.cmd
```

`install.cmd` 会通过 `uv python find` 选择实际安装的兼容解释器，不依赖固定的 `py -3.x` 命令。

验证：

```cmd
.venv\Scripts\aicp.exe doctor
.venv\Scripts\aicp.exe context list
```

## 5. 安装 Multica 并接入 OpenCode

Multica Service 可以使用 Cloud，也可以 Self-host；Multica CLI/Daemon 必须安装在拥有代码、OpenCode 和本地工具链的用户电脑上。

### 5.1 安装 CLI

macOS/Linux：

```bash
brew install multica-ai/tap/multica
```

没有 Homebrew 时：

```bash
curl -fsSL https://raw.githubusercontent.com/multica-ai/multica/main/scripts/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/multica-ai/multica/main/scripts/install.ps1 | iex
```

确认安装：

```bash
multica version
```

### 5.2 连接 Cloud 或 Self-host

Multica Cloud：

```bash
multica setup cloud
multica daemon status
```

Self-host Multica Service：

```bash
git clone --depth 1 https://github.com/multica-ai/multica.git
cd multica
make selfhost
docker compose -f docker-compose.selfhost.yml ps
curl -fsS http://localhost:8080/readyz
```

然后在开发者电脑连接 Self-host Service：

```bash
multica setup self-host \
  --server-url https://api.example.com \
  --app-url https://app.example.com
```

完整的跨平台安装、首次登录、更新和故障排查步骤见 [中文使用指南](README.zh-CN.md#4-安装-multica)。

### 5.3 配置 Multica 与 OpenCode

先在 Multica 中完成以下配置，具体创建命令以当前安装的 Multica 版本为准：

1. 登录 Multica，并确保 `multica version`、`multica auth status`、`multica daemon status` 正常。
2. 创建或选择 Workspace 和 Project。
3. 为 Project 添加 `local_directory` 或 `github_repo` Project Resource。
4. 在 Multica 中配置使用 OpenCode 的 Agent，并让 Multica Daemon 能找到本机 `opencode` 可执行文件及目标仓库。
5. 由 Multica 创建和执行 Task/Run。AICP 不直接启动 OpenCode，也不保存 OpenCode 会话副本。

AICP 只读验证：

```bash
uv run aicp multica-workspaces
uv run aicp multica-projects --workspace <workspace-slug-or-id>
uv run aicp context resources <context-id>
uv run aicp issue-runs MUL-128 --context <context-id>
uv run aicp run-messages <task-id> --context <context-id>
```

如果用户只提供 `MUL-128` 而没有 Context，AICP 会依次查询已配置的 Context/Multica Binding；不要求整台电脑只存在一个 Workspace。

## 6. 配置 Context

打开 `http://127.0.0.1:8765/admin`：

1. 创建 `asset-center`，名称填写 `Asset Center`，别名可填 `asset center, 媒资中心, asset`。
2. 加载 Multica Workspace 和 Project，将主要 Project 绑定为 `primary`，其他相关 Project 绑定为 `related`。
3. 需要领域知识时，只填写既有 Open WebUI Knowledge ID；AICP 不上传、索引或查询 Knowledge 内容。
4. 点击“读取 Project Resources”确认资源来自 Multica 实时结果。

CLI 等价操作：

```bash
uv run aicp context add asset-center --name "Asset Center" --aliases "asset center,媒资中心,asset"
uv run aicp context bind-multica asset-center --workspace <workspace> --project <project-id> --role primary
uv run aicp context bind-knowledge asset-center --knowledge-id <knowledge-id> --label "Asset Center Domain"
uv run aicp brief "看看媒资中心现在什么情况"
```

## 7. Open WebUI User Tool Server

在每个用户自己的 Open WebUI 设置中添加 OpenAPI User Tool Server：

```text
Server URL: http://127.0.0.1:8765
Schema URL: http://127.0.0.1:8765/openwebui/openapi.json
ID: aicp
```

必须使用 `/openwebui/openapi.json`，不能使用完整的 `/openapi.json`。浏览器直接请求本机 AICP，Linux Open WebUI 服务端不需要访问用户电脑。

模型应发现六个工具：

```text
aicp_workspace_list_contexts
aicp_workspace_context_brief
aicp_workspace_today
aicp_workspace_issue_detail
aicp_workspace_issue_runs
aicp_workspace_run_messages
```

推荐 System Instruction 见 [OPENWEBUI_SETUP.md](OPENWEBUI_SETUP.md)。Knowledge 问题必须由 Open WebUI 根据 `knowledge_query_plan` 调用自己的 `query_knowledge_files`。

## 8. 验收

先运行自动验证：

```bash
uv sync --group dev
uv run pytest -q
```

再在 Open WebUI 中逐条测试：

1. `列出我配置的工作上下文`
2. `看看 Asset Center 现在什么情况`
3. `Asset Center 当前有什么风险？`
4. `MUL-128 现在什么状态？`
5. `MUL-128 上次为什么失败？`
6. `Asset Center 的接口设计规范是什么？`
7. `今天我应该先处理什么？`

检查第 6 项时，AICP 响应只能包含 Knowledge ID 和查询计划，实际文档命中必须来自 Open WebUI。检查第 5 项时，调用链应为 Issue Detail → Issue Runs → Failed Run Messages。任何创建、更新、分配、重试或删除操作均不属于第一阶段。

## 9. 常见故障

- `multica executable was not found`：确认 `multica` 在启动 AICP 的 shell/PATH 中可见。
- 多 Workspace 下 Issue 查询失败：优先给工具传 `context_id`；未传时 AICP 会扫描已配置 Binding。
- Open WebUI 看不到六个工具：确认配置的是 User Tool Server，并使用 `/openwebui/openapi.json`。
- Knowledge 没有命中：确认 Context 绑定的是现有 Knowledge ID，并启用 Open WebUI 内置 Knowledge 工具。
- 浏览器无法访问 AICP：确认 `http://127.0.0.1:8765/health` 在同一浏览器所在电脑可访问，并检查本机安全软件是否拦截回环请求。
