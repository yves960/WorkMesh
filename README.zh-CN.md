# Personal AI Control Plane 中文使用指南

Personal AI Control Plane（AICP）是一层轻量的个人工作上下文控制面，用于连接 Open WebUI 与 Multica。

它只负责：

- 根据名称和别名识别用户正在谈论的工作 Context。
- 将 Context 映射到既有的 Multica Workspace、Project 和 Open WebUI Knowledge。
- 聚合 Issue、Run、Project Resource 等实时状态。
- 为 Open WebUI 提供受控的只读 Agent Tool。

AICP 不创建第二套项目管理、知识库或 Agent 执行平台。

## 1. 架构与事实源

```text
Open WebUI
├── Chat / Model / Assistant
├── Knowledge / RAG / Memory
└── User Tool Server
          │
          ▼
AICP（用户本机 127.0.0.1:8765）
├── Context Resolver
├── Intent Router
├── Today Aggregator
└── System Binding
          │
          ▼
Multica CLI / Daemon
├── Workspace / Project / Issue
├── Task / Run / Agent
└── OpenCode 等 Coding Agent
```

事实源必须保持唯一：

| 系统 | 负责内容 |
| --- | --- |
| Open WebUI | AI 交互、Knowledge/RAG、Memory、用户与权限 |
| Multica | Workspace、Project、Issue、Task/Run、Agent 和执行历史 |
| AICP | Context、别名、跨系统引用、意图路由和动态聚合 |

## 2. 前置条件

AICP 支持 macOS 和 Windows。

需要准备：

- `uv`。
- 满足 `pyproject.toml` 中 `requires-python` 范围的本机 Python。
- 需要读取真实工作状态时，安装并登录 Multica CLI。
- 需要执行编码任务时，由 Multica Daemon 配置并调用 OpenCode。

安装脚本不会固定或下载某个 Python 小版本，而是使用本机已经安装的兼容解释器。

## 3. 安装 Open WebUI

本方案将 Open WebUI 部署在 Linux Server，并通过浏览器为团队提供统一入口。以下步骤使用 Python 虚拟环境，不使用 Docker。

Open WebUI 对 Python 的兼容范围会随版本变化。安装前请查看 [Open WebUI 官方 Quick Start](https://docs.openwebui.com/getting-started/quick-start/)；下面的命令使用服务器实际安装的 `python3`，不在脚本中固定小版本。

### 3.1 创建运行用户和目录

```bash
sudo useradd --system \
  --home /opt/open-webui \
  --shell /usr/sbin/nologin \
  openwebui

sudo install -d -o openwebui -g openwebui /opt/open-webui
sudo install -d -o openwebui -g openwebui /data/open-webui
```

如果 `openwebui` 用户已经存在，跳过 `useradd`。

### 3.2 使用实际安装的 Python 创建虚拟环境

```bash
PYTHON_BIN=$(command -v python3)
"$PYTHON_BIN" --version

sudo -u openwebui "$PYTHON_BIN" -m venv /opt/open-webui/.venv
sudo -u openwebui /opt/open-webui/.venv/bin/pip install --upgrade pip
sudo -u openwebui /opt/open-webui/.venv/bin/pip install open-webui
```

确认安装：

```bash
/opt/open-webui/.venv/bin/open-webui --help
```

### 3.3 安装并启动 systemd 服务

仓库已经提供 [open-webui.service](deploy/open-webui/open-webui.service)：

```bash
sudo install -m 0644 deploy/open-webui/open-webui.service \
  /etc/systemd/system/open-webui.service

sudo systemctl daemon-reload
sudo systemctl enable --now open-webui
sudo systemctl status open-webui
```

查看日志：

```bash
sudo journalctl -u open-webui -f
```

浏览器打开：

```text
http://<linux-server>:8080
```

第一个注册账号会成为管理员。随后在管理员设置中配置模型 Provider、用户、Group、Knowledge 和 Web Search。生产环境应在 Open WebUI 前配置 HTTPS 反向代理。

### 3.4 更新 Open WebUI

更新前先备份 `/data/open-webui`，再执行：

```bash
sudo systemctl stop open-webui
sudo -u openwebui /opt/open-webui/.venv/bin/pip install --upgrade open-webui
sudo systemctl start open-webui
sudo journalctl -u open-webui -n 100 --no-pager
```

官方更新说明：[Updating Open WebUI](https://docs.openwebui.com/getting-started/updating/)。

## 4. 安装 Multica

Multica 包含两个部分：

- Multica Service：Web、API 和数据库，可以使用 Multica Cloud，也可以自行部署。
- Multica CLI/Daemon：安装在开发者电脑上，连接 Workspace，并在本机调用 OpenCode 等 Coding Agent。

即使 Multica Service 部署在服务器，Daemon 和 Coding Agent 仍应运行在实际拥有代码与工具链的开发者电脑上。

### 4.1 安装 Multica CLI

macOS 或 Linux 使用 Homebrew：

```bash
brew install multica-ai/tap/multica
```

没有 Homebrew 时，可以使用官方安装脚本：

```bash
curl -fsSL \
  https://raw.githubusercontent.com/multica-ai/multica/main/scripts/install.sh \
  | bash
```

Windows PowerShell，无需管理员权限：

```powershell
irm https://raw.githubusercontent.com/multica-ai/multica/main/scripts/install.ps1 | iex
```

Windows 安装完成后需要打开一个新终端，使 `%USERPROFILE%\.multica\bin` 进入 `PATH`。

验证：

```bash
multica version
```

官方安装说明：[Multica CLI Installation Guide](https://github.com/multica-ai/multica/blob/main/CLI_INSTALL.md)。

### 4.2 连接 Multica Cloud

如果使用 Multica Cloud，执行一键设置：

```bash
multica setup cloud
```

也可以分步执行：

```bash
multica login
multica auth status
multica daemon start
multica daemon status
```

`multica login` 会打开浏览器完成认证。无浏览器环境可以在 Web UI 创建 Personal Access Token，然后使用交互式 Token 登录，避免 Token 留在 shell 历史中：

```bash
multica login --token=
```

### 4.3 Self-host Multica Service

服务器需要 Docker Engine 或 Docker Desktop、Compose v2、Git、Make、curl 和 OpenSSL，并确保本机端口 `3000`、`8080` 可用。

```bash
docker info
docker compose version

git clone --depth 1 https://github.com/multica-ai/multica.git
cd multica
make selfhost
```

`make selfhost` 会生成 `.env`、随机密钥和数据库密码，拉取 PostgreSQL、backend、frontend 镜像并创建持久卷。

验证：

```bash
docker compose -f docker-compose.selfhost.yml ps
curl -fsS http://localhost:8080/readyz
```

预期 `/readyz` 返回数据库和迁移均为 `ok`。浏览器打开 `http://localhost:3000` 完成首次登录并创建 Workspace。

在运行 Coding Agent 的电脑上安装 CLI 后执行：

```bash
multica setup self-host
multica daemon status
```

如果 Service 在另一台机器：

```bash
multica setup self-host \
  --server-url https://api.example.com \
  --app-url https://app.example.com
```

远程部署不要直接把 `3000`、`8080` 暴露到公网，应使用 HTTPS 反向代理。完整说明：[Multica Self-host Quickstart](https://multica.ai/docs/self-host-quickstart)。

### 4.4 更新和排查

更新 CLI：

```bash
brew upgrade multica-ai/tap/multica
# 非 Homebrew 安装
multica update
```

Daemon 常用命令：

```bash
multica daemon restart
multica daemon status
multica daemon logs -f
```

Self-host 更新前应先备份 PostgreSQL，再拉取配置和镜像：

```bash
cd multica
git pull --ff-only
docker compose -f docker-compose.selfhost.yml pull
docker compose -f docker-compose.selfhost.yml up -d
curl -fsS http://localhost:8080/readyz
```

Daemon 会以当前操作系统用户的权限执行 Agent。生产使用时应采用专用用户、容器或虚拟机隔离，不要让 Daemon 获得超出目标仓库所需的权限。

## 5. macOS 安装 AICP

安装 `uv`：

```bash
brew install uv
```

在项目目录执行：

```bash
chmod +x install.sh run-api.sh
./install.sh
./run-api.sh
```

安装脚本会显示实际选中的 Python，例如：

```text
Using Python 3.x.x at /path/to/python3
```

## 6. Windows 安装 AICP

先安装 `uv` 和一个兼容的 Python，然后在项目目录执行：

```cmd
install.cmd
run-api.cmd
```

`install.cmd` 会通过 `uv python find` 选择本机兼容解释器，不依赖固定的 `py -3.x` 命令。

## 7. 启动后验证

访问：

```text
管理页面：http://127.0.0.1:8765/admin
完整 API：http://127.0.0.1:8765/docs
Agent OpenAPI：http://127.0.0.1:8765/openwebui/openapi.json
```

健康检查：

```bash
curl http://127.0.0.1:8765/health
```

CLI 检查：

```bash
uv run aicp doctor
uv run aicp context list
```

数据库默认位于：

```text
data/control-plane.db
```

该数据库只保存 Context 和系统绑定，不保存 Multica 的业务实体副本，也不保存 Knowledge 内容。

## 8. 配置 Context

打开 `http://127.0.0.1:8765/admin`，然后：

1. 创建 Context，例如 `asset-center`。
2. 填写名称 `Asset Center`。
3. 填写别名，例如 `asset center, 媒资中心, asset`。
4. 加载 Multica Workspace 和 Project。
5. 将主要 Project 绑定为 `primary`，相关 Project 绑定为 `related`。
6. 如需领域知识，只绑定已经存在的 Open WebUI Knowledge ID。

CLI 等价操作：

```bash
uv run aicp context add asset-center \
  --name "Asset Center" \
  --aliases "asset center,媒资中心,asset"

uv run aicp context bind-multica asset-center \
  --workspace <workspace> \
  --project <project-id> \
  --role primary

uv run aicp context bind-knowledge asset-center \
  --knowledge-id <knowledge-id> \
  --label "Asset Center Domain"
```

Project Resource 始终从 Multica 实时读取。AICP 不建立 Git 仓库或本地目录 Registry。

## 9. 接入 Open WebUI

在每个用户自己的 Open WebUI 设置中添加 User Tool Server：

```text
Server URL: http://127.0.0.1:8765
OpenAPI Schema: http://127.0.0.1:8765/openwebui/openapi.json
Tool Server ID: aicp
```

必须使用 User Tool Server，而不是由 Linux 服务器直接访问用户电脑。请求链路为：

```text
Open WebUI Server → 用户浏览器 → localhost:8765 → AICP
```

第一阶段只向聊天 Agent 暴露六个只读工具：

```text
workspace_list_contexts
workspace_context_brief
workspace_today
workspace_issue_detail
workspace_issue_runs
workspace_run_messages
```

不要把完整的 `/openapi.json` 配置给聊天 Agent，因为其中包含管理 API。

## 10. Knowledge 查询

当 `workspace_context_brief` 返回：

```json
{
  "knowledge_query_plan": {
    "bound": true,
    "tool": "query_knowledge_files",
    "knowledge_ids": ["knowledge-id"],
    "query": "Asset Center 的接口设计规范是什么？"
  }
}
```

Open WebUI 应继续使用自己的 Knowledge 工具完成 RAG 查询。AICP 只返回绑定和查询计划，不读取文档、不生成 Embedding，也不保存向量。

## 11. Multica 与 OpenCode

OpenCode 由 Multica 管理，AICP 不直接启动或注册 OpenCode。

推荐链路：

```text
Multica Issue
→ Multica Task / Run
→ Multica Agent / Daemon
→ OpenCode
→ OpenSpec / Beads / Code
```

准备步骤：

1. 确认 `multica version`、认证和 Daemon 状态正常。
2. 在 Multica 中创建或选择 Workspace 与 Project。
3. 为 Project 配置 `local_directory` 或 `github_repo` Resource。
4. 在 Multica 中配置使用 OpenCode 的 Agent。
5. 确保 Multica Daemon 能找到本机 `opencode` 可执行文件和目标仓库。
6. 由 Multica 创建 Task/Run 并保存执行历史。

AICP 只读检查：

```bash
uv run aicp multica-workspaces
uv run aicp multica-projects --workspace <workspace>
uv run aicp context resources <context-id>
uv run aicp issue-runs MUL-128 --context <context-id>
uv run aicp run-messages <task-id> --context <context-id>
```

## 12. Linux 部署 Open WebUI

Open WebUI 可以部署在 Linux Server，AICP 继续运行在每个用户的电脑上。

使用服务器现有且与 Open WebUI 兼容的 Python：

```bash
useradd --system --home /opt/open-webui --shell /usr/sbin/nologin openwebui
install -d -o openwebui -g openwebui /opt/open-webui /data/open-webui

PYTHON_BIN=$(command -v python3)
"$PYTHON_BIN" --version
"$PYTHON_BIN" -m venv /opt/open-webui/.venv

/opt/open-webui/.venv/bin/pip install --upgrade pip open-webui
chown -R openwebui:openwebui /opt/open-webui /data/open-webui
```

安装仓库提供的 systemd 单元：

```bash
install -m 0644 deploy/open-webui/open-webui.service \
  /etc/systemd/system/open-webui.service
systemctl daemon-reload
systemctl enable --now open-webui
systemctl status open-webui
```

生产环境建议在 Open WebUI 前配置 HTTPS 反向代理。

## 13. 第一阶段验收

在 Open WebUI 中依次验证：

1. `列出我配置的工作上下文`
2. `看看 Asset Center 现在什么情况`
3. `Asset Center 当前有什么风险？`
4. `MUL-128 现在什么状态？`
5. `MUL-128 上次为什么失败？`
6. `Asset Center 的接口设计规范是什么？`
7. `今天我应该先处理什么？`

其中第 6 项必须由 Open WebUI Knowledge 工具完成最终查询；AICP 只负责解析 Context 和返回 Knowledge Binding。

## 14. 常见问题

### Open WebUI 看不到六个工具

确认使用的是：

```text
http://127.0.0.1:8765/openwebui/openapi.json
```

并确认配置类型是 User Tool Server。

### AICP 找不到 Multica

在启动 AICP 的同一个终端中执行：

```bash
multica version
uv run aicp doctor
```

确保 Multica 位于 `PATH`。

### Context 无法解析

检查 Context 的名称和 aliases，避免多个 Context 使用相同或过于宽泛的别名。

### 本机 Python 不兼容

安装一个满足 `pyproject.toml` 中 `requires-python` 范围的 Python，然后重新执行安装脚本。无需修改脚本中的版本号。

## 15. 安全边界

当前阶段禁止自动写入 Multica：

- 不创建或更新 Issue。
- 不分配 Agent。
- 不启动或重试 Run。
- 不因普通聊天执行写操作。

未来启用写操作时，必须增加显式确认、权限检查和操作审计。

更多细节参见：

- [完整实施方案](IMPLEMENTATION_GUIDE.md)
- [Open WebUI 配置](OPENWEBUI_SETUP.md)
- [系统职责边界](CAPABILITY_BOUNDARIES.md)
- [v0.3 升级说明](UPGRADE_FROM_V0.3.md)
