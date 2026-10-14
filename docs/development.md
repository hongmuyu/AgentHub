# AgentHub 本地开发指南

本文用于修改后端、前端或示例 Agent。只想运行项目时优先使用 [README 的 Docker 部署](../README.md#部署运行第一次请按顺序完成)；两种方式使用相同的模型配置和业务接口。以下以 Ubuntu / Linux 开发环境为例。

## 开始前：Fork 与克隆

先在 [AgentHub 仓库](https://github.com/hongmuyu/AgentHub)点击 **Fork**，将仓库复制到自己的账号；再克隆自己的 fork。已有本地仓库时跳过克隆。

```bash
git clone https://github.com/你的GitHub用户名/AgentHub.git
cd AgentHub
git remote -v
```

用户名是占位符，必须替换；`origin` 应指向你自己的 fork。阅读项目当前分支的 README，确认环境配置后再改代码。需要向原仓库贡献时，在自己的 fork 建立功能分支，完成聚焦改动后提交并创建 PR，不要把本地私有配置一并提交。

## 1. 开发环境

- Python **3.12**：项目声明 `>=3.12,<3.13`，不要直接使用系统 Python 3.13。
- `uv`：按 [uv 官方安装文档](https://docs.astral.sh/uv/getting-started/installation/)安装，用于同步 `uv.lock`。
- Node.js **24** 与 npm：与前端 Dockerfile 保持一致。
- Linux / Ubuntu 开发工具及 Cairo 开发库：`pycairo` 所在依赖链需要它。
- Git，以及已填写的仓库根目录 `.env`。模型配置完整示例见 [README](../README.md#2-创建并填写根目录-env)。

以下系统依赖命令适用于 Ubuntu / Debian；其他系统需安装等价依赖，或使用 Docker。各终端首先进入自己的 AgentHub 仓库目录。

```bash
sudo apt-get update
sudo apt-get install -y build-essential pkg-config python3-dev libcairo2-dev
uv sync --frozen --python 3.12
.venv/bin/python --version
node --version
npm --version
```

`uv sync` 会创建/补齐 `.venv`，不会运行测试。若出现 `Dependency "cairo" not found`，检查 `libcairo2-dev` 是否安装，再重试同步；不要删改依赖锁文件。镜像构建成功并不代表宿主机也已安装 Cairo。

## 2. 配置与启动后端（终端 A）

确保根目录 `.env` 包含聊天三项与五项 Embedding 配置及实际密钥变量。后端从当前工作目录加载 `.env`，因此必须在仓库根目录运行；已存在于进程环境的同名变量优先于 `.env`。不要直接把 Docker 专用 `.env.docker` 当成宿主机环境，它的 `backend` 主机名只在 Compose 网络中有效。

如果 Compose 正占用 `6400` / `5173`，先在根目录停止它：

```bash
docker compose stop
```

该步骤仅适用于此前使用过 Compose 的情况。确保宿主机用户能写 `data/`、`logs/`、`WareHouse/`；与容器共用旧文件时先检查归属，不要删除数据。

```bash
.venv/bin/python server_main.py --host 127.0.0.1 --port 6400
```

需要后端源码热重载时，在上面命令末尾加 `--reload`。重载会重启进程，调试真实任务时应避免运行中修改代码。省略 `--port` 时原生入口默认使用 `8000`，与前端默认代理 `6400` 不一致，因此这里显式指定。

## 3. 安装并启动前端（终端 B）

从仓库根目录执行：

```bash
cd frontend
npm ci --no-audit --no-fund
VITE_API_BASE_URL=http://127.0.0.1:6400 npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

保持终端 A、B 运行。`--strictPort` 在端口占用时直接报错，避免前端悄悄换端口。Vite 将 `/api` 与 `/ws` 代理到本机后端；不要在本地开发时把代理地址设为 `http://backend:6400`。上述行内环境变量写法适用于 Linux / macOS / WSL2 shell。

需要生成前端构建产物时，可在 `frontend/` 运行 `npm run build`，产物位于 `frontend/dist/`。这不是启动开发服务的前置步骤。

## 4. 登记目录并开始使用（终端 C）

在仓库根目录先确认后端响应，再登记六个示例 Agent：

```bash
curl -fsS http://127.0.0.1:6400/health
.venv/bin/python -c 'from utils.env_loader import load_dotenv_file; load_dotenv_file(); import runpy; runpy.run_module("server.services.agenthub.demo_catalog", run_name="__main__")'
curl -fsS 'http://127.0.0.1:6400/api/agenthub/agents?limit=100'
```

**登记入口本身不会先加载 `.env`**，因此宿主机命令显式调用已有 `load_dotenv_file()`；Compose 命令不需要这个包装，因为容器环境已经加载配置。不要直接 `source .env`：本项目的配置文件应按数据读取，不当作 shell 脚本执行。

登记会调用真实 Embedding 服务并写入 `data/agenthub.db`。完成后打开 [Agent 管理](http://localhost:5173/agenthub/registry)和[任务页面](http://localhost:5173/launch)，按 README 提交第一条 `semantic` 任务。修改根目录 `.env` 后需要重启宿主机后端进程；热重载不保证监听该文件。

## 5. 修改代码时从哪里入手

- [元数据](../server/services/agenthub/metadata.py)、[版本管理](../server/services/agenthub/versions.py)、[Registry](../server/services/agenthub/registry.py)：业务 Agent 身份、不可变版本、注册与启停。
- [Discovery](../server/services/agenthub/discovery.py)、[Router](../server/services/agenthub/router.py)、[重排适配器](../server/services/agenthub/reranker.py)：有效候选、语义召回、阈值拒绝和候选内重排。
- [任务服务](../server/services/agenthub/task_service.py)、[执行适配器](../server/services/agenthub/workflow_dispatcher.py)、[状态转换](../server/services/agenthub/run_transitions.py)：业务运行与既有 Workflow Runtime 的集成。
- [应用装配](../server/services/agenthub/app_setup.py)：环境配置、校准校验、服务和路由策略装配。
- [后端路由](../server/routes/)、[前端源码](../frontend/src/)、[中文界面文案](../frontend/src/locales/zh.json)：API 和页面入口。
- [示例 Agent 目录](../yaml_instance/agenthub_catalog.md)、[评测指南](../evaluation/guide.md)、[Demo 指南](../demo/guide.md)：示例元数据、评测与演示。

复用上游 Workflow、节点类型、工具、附件、日志与 WebSocket；业务 Registry / Router 放在应用服务层，不应塞进图执行器。业务 `success` 必须同时满足正常 Workflow 完成和目标 Agent 的有效结构化成功结果，不能只检查原生 `completed`。

## 6. 新增一个兼容的示例 Agent

1. 参照 `yaml_instance/agenthub_*_metadata.json` 定义名称、描述、能力、工具、标签及逻辑 `runtime_ref`，写清能力边界。
2. 参照现有 `agenthub_*.yaml` 编写受控薄 Workflow，复用 `type: agent` 及现有配置；经校验的目标只能是当前薄 Workflow 合约支持的形式。
3. 在服务端 [manifest](../yaml_instance/agenthub_manifest.json) 登记逻辑引用与文件映射。客户端不能自行提交任意本地路径。
4. 将 Workflow 和映射保存到后端使用的 `yaml_instance/`，在 Registry UI/API 注册元数据；若新增的是符合命名约定的目录 fixture，也可使用目录登记命令。注册时校验 Workflow 并建立向量索引。
5. 在页面查看能力、状态和版本，再提交相关任务观察候选和结果。新增兼容 Agent 不应要求修改 Router 的名称分支。

元数据更新会产生新版本并更新索引；启停改变候选资格，不能删除历史运行证据。改变 Embedding 模型需要重新校准与索引处理，不能直接沿用旧阈值，详见[运行配置](agenthub.md#standard-docker-setup)。

## 7. 文档与提交范围

AgentHub 面向用户的部署、开发、Demo、评测说明使用中文；代码标识、命令、API 和错误码保留原名。上游 `docs/user_guide/en/` 与 `frontend/public/tutorial-en.md` 是保留的英文参考/界面资源，项目入口优先链接对应中文文档。

历史验证范围见[运行指南](agenthub.md#verified-deployment-scope-and-limits)，评测数据和模型配置来源见[评测指南](../evaluation/guide.md)。

提交时只包含本次改动所需文件，不提交 `.env`、数据库、日志、工件和构建产物。文档修改可检查链接与 `git diff --check`；正常运行项目不需要先跑测试或基准评测。功能变更的测试要求仍按项目开发约定执行，不能从历史限定范围的通过结果推断完整套件通过。
