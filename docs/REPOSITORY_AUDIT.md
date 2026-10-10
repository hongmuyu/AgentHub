# AgentHub 仓库发布前审计 — 2026-10-10

审计起点为 `agenthub-dev` 提交 `0ac03f1ff5a23f6f23f0a2e3cf8189bc532844a5`；工作区起初干净，与 `origin/agenthub-dev` 一致。`main` / `origin/main` / `v1.0.0^{}` 均为 M4 合并提交 `847f973185807f6e074092199c692cd43ea1a804`，远端默认分支为 main。本轮只整理忽略规则与文档，未改动业务实现、依赖版本、数据库或稳定标签。

## 文件分类与数量

起点 `git ls-files` 共 **741 个文件、167,986,798 bytes（约 160.2 MiB）**。按固定 ChatDev 基线 `4fb2db0ea90375ce1059f44fe03ffbd191a7a169` 比较 Git blob：**597 个相同、16 个修改、128 个新增、0 个上游文件删除**。这描述审计起点，不是对最新上游版本的同步状态。

- Python/运行服务相关目录 `runtime/`、`server/`、`workflow/`、`entity/`、`check/`、`schema_registry/`、`utils/`、`functions/`、`tools/`、`mcp_example/`：219 个。
- `frontend/`：296 个，包含源码、测试、依赖锁文件和界面资源。
- `tests/`：46 个；`yaml_instance/` 与 `yaml_template/`：60 个。
- `assets/`：26 个；`docs/`：48 个；`evaluation/`：12 个；`demo/`：5 个。
- 根文件：23 个；`.agents/`：5 个；`.github/`：1 个。

处置分类如下；待确认事项可能引用保留文件，不与上述目录统计相加：

1. **必须保留/本轮保留：全部 741 个原有跟踪文件。** 保留 ChatDev 源码、Workflow 示例、测试、依赖锁文件、图片/字体/教程、Apache `LICENSE` 和 Inter `OFL.txt`；保留 AgentHub 设计与验收文档、评测数据/脚本/原始报告、Demo 脚本/JSON/HTML/视频。新增本审计文档后为 742 个跟踪文件。
2. **应由 .gitignore 保护：本地环境、运行数据、依赖、缓存与临时输出。** 起点有 20,912 个已忽略文件，0 个未忽略的未跟踪文件。原规则已保护 `.env`、`data/`、`logs/`、`WareHouse/`、虚拟环境、`node_modules/` 和前端 `dist/`；补齐环境变体、根级工具缓存与测试输出规则。
3. **已跟踪但建议取消跟踪：0 个。** `git ls-files -ci --exclude-standard` 无输出；没有执行 `git rm --cached`。没有跟踪真实 `.env`、数据库、日志、缓存或构建目录。
4. **可安全整理：两个根级工具缓存，15 个文件。** 删除 `.pytest_cache/` 的 5 个文件和 `.ruff_cache/` 的 10 个文件，合计 70,855 bytes。删除前确认两个目录不含 Git 跟踪文件且不是符号链接。保留 `data/`、`WareHouse/`、日志、依赖目录、已有 Python bytecode 和前端构建输出；未删除 Docker volumes。
5. **需要人工决定：历史链接/媒体资源整理、Git 内部临时 pack、既有构建目录权限及公开个人信息。** 详见后文；未自动迁移、删除或重写历史。

`.agents/skills/` 不是可直接删除的本机配置：`runtime/node/agent/skills/manager.py` 将它作为默认技能目录，`yaml_instance/skills.yaml` 引用了 `python-scratchpad` 和 `rest-api-caller`。

## 忽略规则与对外入口

根 `.gitignore` 原有规则覆盖 `__pycache__/`、`*.pyc`、`.venv/`、`venv/`、`env/`、`.uv-cache/`、`.idea/`、`.vscode/`、`frontend/.vscode/`、`.DS_Store`、`.env`、`logs/`、`node_modules/`、`data/`、`temp/`、`WareHouse/`。前端自身的 `.gitignore` 已覆盖 `dist`、`dist-ssr`、日志、`*.local` 和编辑器文件。

本轮根 `.gitignore` 增加 9 条规则：`.pytest_cache/`、`.ruff_cache/`、`.coverage`、`.coverage.*`、`htmlcov/`、`.env.*`、`!/.env.example`、`!/.env.docker`、`*.log`。两个例外只保留经过核对的根级环境模板；没有笼统忽略 JSON、YAML、evaluation 或 demo。

`.gitignore` 控制未跟踪文件是否进入 Git，不能移除已经跟踪的文件；`.dockerignore` 控制 Docker build context，两者用途不同。根 `.dockerignore` 增加两个工具缓存、coverage 输出和 `*.log` 的排除；`frontend/.dockerignore` 增加 `dist/`、`dist-ssr/`。环境文件继续不进入镜像构建上下文，Compose 通过宿主机 `env_file` 加载 `.env` / `.env.docker`，无需把它们 COPY 进镜像。

两份根 README 新增 AgentHub 部署、Release Notes、Benchmark、Demo、Portfolio 和 Interview Guide 导航，保留原 ChatDev 内容和署名。修复六处目标明确的相对链接：中英文 README 的 Tooling `index.md` 改为现有 `README.md`；中英文 dynamic_execution 的 workflow_authoring 与 nodes/agent 链接去掉多余的 `../`。

## 必须保留的 AgentHub 复现材料

- [架构和部署](agenthub.md)、[冻结设计](../AGENTHUB_DESIGN.md)、[任务清单](../TASKS.md)、[开发基线](../DEVELOPMENT_BASELINE.md)、[架构勘察](../ARCHITECTURE_RECON.md)。
- [六 Agent manifest](../yaml_instance/agenthub_manifest.json)及对应的六个 `agenthub_*.yaml` 与六个 `agenthub_*_metadata.json`；[目录登记实现](../server/services/agenthub/demo_catalog.py)。全部路径存在且被跟踪。
- [evaluation](../evaluation/README.md)下全部 12 个跟踪文件，包括两个数据集、三个 runner、校准/独立测试、attempt1 和 live/offline 规模原始报告。attempt1 是错误与参数来源证据，不作为垃圾删除。
- [demo](../demo/README.md)下全部 5 个跟踪文件，包括 `t48_reproduce.py`、原始五条 Run 的 JSON、证据展示 HTML 和 `agenthub_t48_demo.mp4`。evaluation 与 demo 均逐文件比较确认和 `v1.0.0` 一致。
- [环境模板](../.env.example)和 [非秘密 Docker 配置](../.env.docker)、根及前端 Dockerfile、Compose、Python/前端依赖锁文件、现有测试与原生资源。

## 大文件及隐私审计

按工作树文件大小统计，25 个跟踪文件超过 1 MiB，3 个超过 10 MiB，0 个超过 50 MiB。最大三个文件均与固定上游基线相同：

- `assets/cases/3d_generation/3d.gif`：16,618,723 bytes。
- `assets/cases/video_generation/video.gif`：16,196,364 bytes。
- `assets/cases/game_development/game.gif`：15,212,750 bytes。

`assets/` 共约 73.4 MiB，`frontend/public/media/` 约 48.2 MiB，字体目录约 19.5 MiB，sprites 约 13.4 MiB。README、前端教程或代码引用了这些资源；部分字体/示例是否可精简需另行确认，不能仅凭大小删除。AgentHub 视频仅 **1,470,124 bytes（约 1.4 MiB）**，本轮保留普通 Git 跟踪，不需要为该视频引入 LFS。未来若迁移上游大资源到 LFS 或 Release 附件，必须先核查引用、许可、离线部署及历史/标签影响，并取得明确批准。

安全检查覆盖起点的 **480 个 UTF-8 文本文件**及固定上游基线之后 AgentHub 历史中的 **223 个文本 blob**：检查私钥、常见 token 模式、带凭据 URL、可疑赋值，并在内存中比对当前私有 `.env` 的凭据是否出现在这些文件/历史对象中。未输出或保存凭据值。命中项经核对为 `.env.example` 占位符、测试样例或变量赋值；未发现已确认的真实凭据泄漏，也未发现当前本地凭据的匹配。

`.env.docker` 只有 `BACKEND_BIND`、`FRONTEND_HOST`、`FRONTEND_PORT`、`VITE_API_BASE_URL`、`CORS_ALLOW_ORIGINS`；`.env.example` 的 API key 是占位符。私有 `.env` 含非空凭据但未被跟踪且已忽略。

潜在披露项：Git 作者/提交者元数据含邮箱，README 保留上游公开贡献者信息，`ARCHITECTURE_RECON.md` 含本地环境路径，评测报告含机器/模型元信息与合成任务，Demo JSON 含 Run UUID、结果哈希和合成任务文本。删除当前文件不能抹除已发布历史。未对全部上游历史、图片/视频逐帧 OCR 或任意未知格式 token 做穷尽审计；本次结果不能等同于无条件的“无敏感信息”保证。

## 验证结果

- **Python：PASS（受限范围）**。从仓库根执行 `env -u AGENTHUB_EMBEDDING_LIVE -u AGENTHUB_RERANK_LIVE PYTHONDONTWRITEBYTECODE=1 timeout 120s .venv/bin/python -m pytest -q --ignore=tests/test_websocket_send_message_sync.py -p no:cacheprovider`：**634 passed、2 skipped**，17.28 秒；一条既有 Requests 依赖兼容警告。两个 skip 是显式未启用的 live provider 测试。包括六 Agent 登记、部署 bootstrap、Benchmark 路径与校准来源回归。
- **完整 Python 套件：NOT VERIFIED**。继承的 `test_websocket_send_message_sync.py` 夹具阻塞仍按开发基线单列；本轮未重跑该阻塞文件、未修改 fixture 或生产 WebSocket 行为。
- **前端测试：PASS**。`node --test frontend/tests/*.test.js`：**42 passed**。
- **前端构建：临时输出目录 PASS；原有 dist 目录受本机权限阻塞**。默认 `npm run build` 在清空 `frontend/dist/assets` 时 EACCES；该目录为 `root:root`、`755`。相同 Vite 构建改用 `npm run build -- --outDir <新建临时目录> --emptyOutDir` 后通过，170 modules，1.92 秒；本轮临时目录自动清理。没有修改现有目录所有权或构建配置。
- **Compose：PASS**。`docker compose config --quiet` 返回 0，不输出展开后的环境值。本轮未重建完整 Docker 镜像、未重启现有服务或重跑真实 provider Demo。
- **资源/规则：PASS**。六项 manifest 均有被跟踪的 workflow/metadata；evaluation/demo 与发布标签一致；两份许可证与上游基线一致；新缓存/私有环境路径正确忽略，两个环境模板不被忽略；没有被新规则遮蔽的已跟踪文件。
- **链接：部分通过，继承缺失已列出**。不含本文的 README/docs/demo/evaluation 共 52 份 Markdown、309 个相对路径引用；修复六处后仍有 **28 处上游继承缺失引用**，分组如下。所有存在的文件目标均被 Git 跟踪；AgentHub 导航和证据文件路径可解析。检查范围是仓库相对文件路径，不包含外站可用性或所有历史 fragment 锚点。
- **差异：PASS**。`git diff --check` 通过；本轮只包含忽略配置与文档变更，没有源码、数据库、凭据或生成输出。

## 待人工确认的整理事项

1. **历史文档引用（28 处）**：中英文 README 各 12 处，涉及 `wiki.md` 的五个历史锚点、两个旧 `WareHouse/` 示例、四个 `visualizer/static/figures/` 图片引用和缺协议的 `www.teachmaster.cn`。两个 dynamic_execution 文档各有一处缺失的 `../edges.md`；两个 function_catalog 文档各有一处缺失的 `file_tool_use_case.yaml`。它们在固定上游基线中已存在。需确认指向 ChatDev 1.0 档案、替代文档还是删去历史描述，不能虚构替代路径。
2. **大资源迁移/精简**：保留原始 GIF、图片、sprites、字体和对应许可证；迁移需单独批准，不移动稳定标签、不自动重写历史。
3. **Git 内部临时文件**：`git count-objects -v` 报告 `.git/objects/pack/tmp_pack_9JTCRq`，约 43.4 MiB。它不属于跟踪文件，也不会作为工作树文件提交；本轮未删除。若要清理，先确认没有正在运行的 Git 操作并进行对象完整性检查，再决定维护操作。
4. **前端已有构建目录权限**：若需要恢复直接写入 `frontend/dist/` 的本机构建，应先确认 root 所属文件的来源与当前服务用途，再决定更改所有权或重建目录。此次仅使用隔离输出完成编译验证。
5. **个人信息与媒体复核**：确认是否接受公开 Git 邮箱、环境路径和机器元信息；需要更强隐私审查时人工检查图片/视频。历史邮箱清除或公开证据改写超出本轮自动整理范围。
