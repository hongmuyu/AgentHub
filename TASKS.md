# AgentHub P0 Implementation Tasks

Status: Approved task decomposition; implementation NOT STARTED.

本文展开已批准并经本次实施前调整的 50-task P0 计划（T00–T49），只定义后续可逐项执行的任务，不表示任何任务已完成。本次仅调整 AGENTS.md/TASKS.md，验证、focused documentation commit 并推送后停止；不得执行 T00，也不得因此合并 main。

AgentHub 面向企业内部 AI Agent 注册、能力发现、动态路由、执行与可观测性。平台管理员登记/管理 Agent，检查能力、版本与状态，启用/禁用并观察平台行为；业务用户提交自然语言任务，由平台发现候选、选择适用 Agent，经 ChatDev Runtime 执行，再查看结果、业务状态与路由 trace。两者是可信本地/内部使用的概念视角，不引入租户、复杂 RBAC、审批或 Marketplace。

## 依据与执行规则

- 任务分解基于已批准的 Plan Mode 文档 `codex-plan-01a0e75c-7134-7473-aa89-dd197a7b40ea-01a0e75c-71.md`；本次按明确授权完善业务验收与 Git 工作流、追加 T49，保留 T00–T48 编号和冻结架构。
- 架构依据：[AGENTHUB_DESIGN.md](AGENTHUB_DESIGN.md)（Status: Design Frozen for P0 Implementation）。下文“设计 §N”均指该文件章节。实施规则见 [AGENTS.md](AGENTS.md)，源码事实见 [ARCHITECTURE_RECON.md](ARCHITECTURE_RECON.md)，环境与继承阻塞见 [DEVELOPMENT_BASELINE.md](DEVELOPMENT_BASELINE.md)。早期概念示例不覆盖冻结设计：UUID/整数版本、六种业务状态、36/12/12 benchmark 分布以冻结设计为准。
- Recon 的源码事实固定于 ChatDev `4fb2db0ea90375ce1059f44fe03ffbd191a7a169`；文档生成时 checkout 为 `agenthub-dev`，HEAD 为 `ce79d43e254bf158bd1437ed5ac15a217e16477a`。这些是参考基线，不替代未来 T00 的现场核验。
- 每个实施会话只处理一个明确选定任务。编号用于稳定引用，不表示可以忽略 Dependencies 按数字顺序实施。Dependencies 均为真实前置条件；分组顺序、时间盒与 Critical Path 是调度说明，不添加隐含依赖。
- 开始单项前读取相关源码，确认可复用组件、现有执行路径和最小插入点。下文明确区分“已有”与“建议新增”；新增路径不是已经存在的实现，也不要求创建空目录或为单用途逻辑拆出多余模块。
- 一次一项，不顺手实现未来任务、不重构无关上游、不静默改变冻结设计。发现真正的设计矛盾则停止该项并记录矛盾；部署 endpoint/model 等配置问题不触发重新设计。
- 测试属于任务完成条件，应随功能交付，不统一拖到 T44。普通自动化使用 fake embedding/reranker、mock provider 与临时 SQLite；live 检查须显式标识。缺失环境或真实服务时写 `NOT VERIFIED`，不得用 mock 成绩替代真实语义质量。
- 每项 Completion evidence 同时适用本节通用规则：任务专项测试 → 相关回归 → 完整检查 `git diff` → 排除无关改动 → focused commit → push `origin/agenthub-dev` → 停止。各任务末尾的 focused commit 均包含此推送要求；不自动开始下一项。保存命令、退出码、结果和脱敏证据。T00 是验证例外：验证证据始终必需，只有实际修改获授权的 tracked 文档才需 commit/push；没有仓库变化则 verify → report → no commit required，报告 HEAD，不创建空/no-op commit。
- 实施任务完成报告必须包括：①任务实现内容；②修改文件；③任务测试；④回归测试；⑤PASS / FAIL / BLOCKED / NOT VERIFIED 项；⑥commit SHA（T00 无改动时为当前 HEAD，并注明无新提交）；⑦push 结果（T00 无改动时不适用）；⑧远端分支；⑨最终 `git status`；⑩COMPLETE 或 BLOCKED。代码变更正常完成要求 focused commit 和成功推送 `origin/agenthub-dev`；未完成必需验收不得标 COMPLETE。可选 live 证据允许 NOT VERIFIED，不能据此声称 live 已通过。
- 不提交凭据，不把 provider 配置、秘密、role/system prompt、YAML 或附件正文写入公开 metadata/trace。保留已有用户改动，提交仅包含本项授权文件。
- `workflow_completed != execution_success`；`Execution Success Rate != Task Quality Success Rate`。执行成功指所选 Agent 按结构化 outcome 合约实际成功执行，需正常 workflow 结束与目标 Agent 结构化成功 outcome；任务质量成功另需断言、rubric 或人工评价，无评价则未测量。拒绝、失败和取消分别保存、统计。SQLite 重启后可查询不等于 Runtime 可续跑。
- 继承基线的 WebSocket fixture 阻塞、host Cairo 缺失、标准前端 Docker 镜像未验证必须分别报告。不能把排除文件的 66 项历史通过当完整 suite 全绿，也不能把 host node_modules 的临时 Compose 覆盖当镜像可复现。

## Block A — Registry & Persistence

时间盒：约 2 天。先完成存储基础，再结合 Block B/C 的引用验证和索引完成 Registry 生命周期。

### T00 — Implementation baseline guard

- **Task ID**: T00
- **Priority**: P0
- **Goal**: 建立可追溯的实施基线，确认当前 checkout、冻结设计、可用验证环境和继承阻塞。
- **Why this task exists**: 后续结果必须能区分 AgentHub 回归与上游或环境问题，避免在错误 revision 或不完整 `.venv` 上开始开发。
- **Dependencies**: 无。
- **Likely files/modules involved**: 已有 `AGENTS.md`、`ARCHITECTURE_RECON.md`、`DEVELOPMENT_BASELINE.md`、`AGENTHUB_DESIGN.md`、`pyproject.toml`、`uv.lock`、Dockerfile、`compose.yml`、`tests/test_websocket_send_message_sync.py`；必要基线证据归入已有 baseline 文档，不新增实现模块。
- **Implementation requirements**: 按设计 §1、§22、§24、§28 复核 branch/HEAD/upstream ancestry、工作区差异和设计状态；记录解释器、依赖环境与可用容器。单列 WebSocket MagicMock fixture 阻塞、host `libcairo2-dev` 问题和标准前端镜像未验证项；只建立基线，不顺手修复上游。验证命令必须有受限时长，使用已核实环境。
- **Do-not-touch boundaries**: 不实现 Registry/Router/outcome，不改生产源码、依赖锁或测试夹具；不覆盖既有文档改动，不借此升级上游。
- **Acceptance criteria**: 报告准确 revision、环境、干净/已有改动边界及每项 passed/failed/blocked/NOT VERIFIED；历史证据与本次复核分开；完整 suite 的阻塞不能被子集通过掩盖。
- **Required tests**: 受限时长的继承测试基线检查，分别记录完整集、WebSocket 文件和相关可通过子集；核对 Python 版本与依赖完整性，必要时复用已验证 Docker 环境。环境不可用时记录阻塞，不伪造通过或安装无关依赖。
- **Completion evidence**: 必须有固定 revision、准确命令/timeout/退出码、环境与阻塞分类；运行本项检查 → 相关基线回归 → 检查 `git diff` → 排除无关改动。只有实际修改获授权的 tracked 基线文档才要求 focused commit 并 push `origin/agenthub-dev`；若没有仓库变化，verify → report → no commit required，只报告验证结果、HEAD 和 git status，不创建空/no-op commit。不得将这些检查写成新功能完成证据。

### T01 — AgentMetadata 合约与最小模块骨架

- **Task ID**: T01
- **Priority**: P0
- **Goal**: 交付独立于 ChatDev AgentConfig 的业务 metadata 和版本输入输出合约。
- **Why this task exists**: 业务身份必须稳定且与模型名/provider/node ID 分离，为存储、发现和历史追踪提供共同边界。
- **Dependencies**: T00。
- **Likely files/modules involved**: 已有 `server/services/`、`entity/configs/node/agent.py`（只读参考）、`tests/`；建议新增 `server/services/agenthub/metadata.py` 与对应窄单元测试，最小包骨架随模型交付。
- **Implementation requirements**: 设计 §3、§4、§7、§23、§26；注册 UUID、初始 version=1、更新递增整数；去空白、规范化和去重，明确有限长度校验；status 只允许 active/disabled。分离身份上的可变 status 与不可变内容快照；UTC 时间；runtime_ref 仅接收逻辑引用格式，实际 allowlist 解析由 T04 完成。拒绝秘密配置字段和明显凭据内容。
- **Do-not-touch boundaries**: 不向 AgentConfig/schema_registry 添加业务字段，不创建六类 executor，不实现数据库、路由或 HTTP API；不硬编码未校准 routing 参数。
- **Acceptance criteria**: 合法 metadata 可规范化读写；空 name/description/capability、过长值、非法状态/引用被拒；重复项按明确规范去重且空能力列表仍非法；模型名与业务名不混淆，状态不属于内容版本快照。
- **Required tests**: 空字段、空白、重复项、每个长度边界及超界、非法 status/runtime_ref、UUID/version 约束、UTC 时间、秘密配置字段拒绝；断言不依赖 provider 初始化。
- **Completion evidence**: 字段规范与边界样例、模型单测命令/结果；运行任务测试 → 相关模型回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T02 — SQLite repository foundation

- **Task ID**: T02
- **Priority**: P0
- **Goal**: 建立独立 AgentHub SQLite 连接、事务和必要初始化边界。
- **Why this task exists**: 业务记录须在重启后可查询，且不能依赖内存 session 或混入画布数据库。
- **Dependencies**: T01。
- **Likely files/modules involved**: 已有 `server/services/vuegraphs_storage.py`（只读先例）、`server/settings.py`（配置风格参考）、`tests/`；建议新增 AgentHub repository/连接管理实现于 `server/services/agenthub/`。
- **Implementation requirements**: 设计 §6、§7、§22；独立数据库文件，启用 WAL、合理 busy timeout、约束与显式事务；支持初始化、连接释放和错误回滚。使用临时数据库验证单后端低并发边界，后续实体在各自任务加入，避免提前建完整 schema。
- **Do-not-touch boundaries**: 不修改 `vuegraphs` 数据/表，不新增 PostgreSQL/向量服务、复杂迁移框架或分布式锁；不创建业务 API。
- **Acceptance criteria**: 初始化可重复执行，连接可关闭并重新打开；事务失败不留下部分写入；受控并发写产生正确结果或可解释的有界错误，不能永久等待。
- **Required tests**: 临时 SQLite 的初始化/约束、提交/回滚、关闭重开、WAL/busy timeout 配置和受控并发；确认独立文件不写入画布 DB。
- **Completion evidence**: 临时 DB 测试命令/结果与事务失败证据；运行任务测试 → 相关持久化回归 → 检查 `git diff` → 排除无关改动 → focused commit，不提交测试数据库。

### T03 — Agent 版本存储

- **Task ID**: T03
- **Priority**: P0
- **Goal**: 持久化 Agent 身份、不可变内容版本和 current version。
- **Why this task exists**: 更新必须保留历史，并为执行快照和原子索引切换提供稳定版本引用。
- **Dependencies**: T02。
- **Likely files/modules involved**: T01/T02 新建的 AgentHub metadata/repository；建议在同一业务包增加 Agent 版本存取与 `tests/` 对应测试。
- **Implementation requirements**: 设计 §3、§4、§6、§7；保证 `(agent_id, version)` 唯一，身份上保存 current version/status；新快照不可覆盖旧内容。提供原子版本插入/切换能力及失败回滚，生命周期编排留给 T08–T10。
- **Do-not-touch boundaries**: 不实现 HTTP、embedding 或 Register 完整编排；不以 status 变化产生内容版本，不修改 ChatDev registry。
- **Acceptance criteria**: 同一 UUID 可读到多个不可变版本；current 指针仅在事务成功后切换；唯一约束和失败回滚有效，关闭重开仍能读取历史及 current。
- **Required tests**: 重复版本、历史修改拒绝、current 切换、事务中途失败、身份状态与快照分离、数据库重开查询。
- **Completion evidence**: 版本前后快照与回滚断言、测试命令/结果；运行任务测试 → 相关 repository 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T08 — Registry Register/Get/List/Search

- **Task ID**: T08
- **Priority**: P0
- **Goal**: 提供完整注册与目录读取服务，只向后续发现暴露验证和索引均成功的条目。
- **Why this task exists**: 防止注册半成品成为候选，并区分目录浏览搜索与任务语义发现。
- **Dependencies**: T03、T05、T07。
- **Likely files/modules involved**: 建议新增 `server/services/agenthub/registry.py`；复用前置 metadata/repository/resolver/index 实现，新增 Registry 服务测试。
- **Implementation requirements**: 设计 §3–§9、§23；Register 校验 metadata、受控 workflow 引用并同步生成 embedding，成功才以 active/version=1 对外可见。Get 返回当前版本/status；List 分页且支持显式包含 disabled/指定状态；Search 只做管理文本查找，不调用任务 Router。
- **Do-not-touch boundaries**: 不增加 HTTP API、审批/硬删除、LLM 决策或 Runtime 执行；不把 Search 命名为 semantic Discovery；不实现未来 Update/启停。
- **Acceptance criteria**: 合法注册后能读到一致版本与索引；引用无效或 embedding 失败无半成品；分页、状态查询、文本 Search 返回预期集合且不泄露配置。
- **Required tests**: 临时 DB+真实 resolver/validator+fake embedding 的注册成功、验证失败、索引失败回滚、Get 未知 ID、List 分页/状态、Search 命中/无结果。
- **Completion evidence**: 注册全链原子性与查询测试结果；运行任务测试 → metadata/repository/resolver/index 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T09 — Registry Update/version switching

- **Task ID**: T09
- **Priority**: P0
- **Goal**: 更新 metadata/runtime_ref 时生成新版本并在验证、索引成功后原子切换。
- **Why this task exists**: 新版本失败不能破坏当前可用目录或改变历史运行的引用。
- **Dependencies**: T08。
- **Likely files/modules involved**: T08 Registry、Agent 版本 repository、T07 index，以及对应生命周期测试。
- **Implementation requirements**: 设计 §3、§4、§9；保留 UUID，版本单调递增；先验证 runtime_ref 并为新内容索引，再切换 current version。失败保持旧版本及旧索引有效；disabled 身份更新后仍 disabled，历史快照只读。
- **Do-not-touch boundaries**: 不重写历史记录，不自动启用 disabled Agent；不做全目录模型迁移（T17）、API 或运行取消。
- **Acceptance criteria**: 合法更新只改变 current 内容版本；旧版本可查且不变；引用/索引任一失败都不切换；disabled 状态跨更新保持。
- **Required tests**: 版本递增、不可变历史、字段及 runtime_ref 更新重索引、索引中途失败/引用校验失败回滚、disabled 更新、未知 Agent。
- **Completion evidence**: 更新成功/失败前后 current/index/history 对照；运行任务测试 → Registry 注册/查询回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T10 — Registry Enable/Disable lifecycle

- **Task ID**: T10
- **Priority**: P0
- **Goal**: 实现目录级启停并维持在途运行快照稳定。
- **Why this task exists**: 能力停用应影响后续候选，而不是改写版本或强制取消已开始的任务。
- **Dependencies**: T09。
- **Likely files/modules involved**: Registry、版本 repository、resolver/index readiness；对应生命周期和快照测试。
- **Implementation requirements**: 设计 §3、§4、§9；Enable 重新验证当前 runtime_ref/workflow 与索引就绪，失败保持 disabled；Disable 使后续 eligible 查询剔除身份；更新身份时间但不增加 version。使用固定版本快照表达既有运行，不要求提前实现 AgentRun。
- **Do-not-touch boundaries**: 不接入运行取消、不删除历史、不以启停生成新版本；不改 session/native status。
- **Acceptance criteria**: enabled/disabled 可正确切换；同一版本号保持，重复操作结果一致；引用失效/索引无效不可启用；已有快照内容与引用不变。
- **Required tests**: 状态转换及重复启停、版本不变、invalid runtime_ref/index 拒绝 Enable、停用后 eligibility 输入剔除、先前取出的运行快照不被修改。
- **Completion evidence**: 状态与版本断言、失效引用证据；运行任务测试 → Registry 生命周期回归 → 检查 `git diff` → 排除无关改动 → focused commit。

## Block B — Runtime Reference & Fixtures

时间盒：约 2 天。Resolver/validator 先供 Registry 使用，demo fixtures 在注册能力具备后交付。

### T04 — runtime_ref manifest、resolver 与路径安全

- **Task ID**: T04
- **Priority**: P0
- **Goal**: 将不透明逻辑引用解析到服务端 allowlist 中受控的 workflow 目标。
- **Why this task exists**: 业务请求不能把任意路径、YAML 或节点表达式变成执行入口。
- **Dependencies**: T01。
- **Likely files/modules involved**: 已有 `server/settings.py`、`server/services/workflow_run_service.py::_resolve_yaml_path()`（参考）；建议新增 `server/services/agenthub/runtime_resolver.py`、manifest 配置及测试 fixture。
- **Implementation requirements**: 设计 §5、§23、§26；服务端管理 `(key, revision)` 到审核文件的映射（设计示例 `workflow://agent-code/1`），以规范化后的真实路径校验 workflow 根边界；不存在、未知 key/revision 及越界均失败。对外只给逻辑 identity 和安全错误，不给物理路径。
- **Do-not-touch boundaries**: 不扩大旧 execute/SDK 路径权限，不开放 YAML upload、不建 artifact registry、不动态生成 DAG；完整结构验证由 T05 完成。
- **Acceptance criteria**: 合法 allowlist 项解析到预期目标；未知引用、绝对路径、穿越、符号链接越界、文件不存在均被拒；客户端错误不包含服务器路径。
- **Required tests**: 临时根目录与 manifest 覆盖合法项、未知 key/revision、绝对路径、`..`、symlink escape、缺失目标、敏感路径不回显；无需 provider。
- **Completion evidence**: 正反路径断言与安全错误样例；运行任务测试 → metadata/reference 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T05 — Thin-workflow validation

- **Task ID**: T05
- **Priority**: P0
- **Goal**: 验证引用目标是可独立运行、恰有一个业务 Agent 的完整静态薄 workflow。
- **Why this task exists**: 一个业务 Agent 不能偷偷映射到多 Agent 团队或不具备输入输出合约的任意节点。
- **Dependencies**: T04。
- **Likely files/modules involved**: 已有 `check/check.py::load_config()`、`validate_design()` / `check_workflow_structure()`（按源码定位）、`entity/configs/graph.py`；扩展 T04 resolver/validator 与 YAML 测试 fixture。
- **Implementation requirements**: 设计 §5、§20、§22；复用现有 YAML loader/validator，核验完整静态图、恰一个 `type: agent`、明确任务输入与有效最终输出。拒绝业务子团队依赖或动态切换 Agent；允许不调用模型且不改变业务 Agent 的确定性输入/输出辅助节点。记录可在执行前核验的 identity/revision 信息。
- **Do-not-touch boundaries**: 不执行 workflow、不初始化 provider，不改图调度/start/end 语义，不切片复杂团队配置。
- **Acceptance criteria**: 合格单 Agent 薄图通过；零/多 Agent、非法 YAML、无有效终点、隐藏子团队依赖被拒；允许的确定性辅助节点可通过并保留明确输入输出。
- **Required tests**: 各种合法/非法 YAML fixture、子团队依赖、辅助节点、输入输出边界；mock/spy 断言校验期间零 provider/Runtime 调用。
- **Completion evidence**: fixture 与校验结果矩阵、无执行断言；运行任务测试 → resolver/原 loader 相关回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T31 — ResearchAgent 薄 workflow 与首个 allowlist fixture

- **Task ID**: T31
- **Priority**: P0
- **Goal**: 交付首个可注册的普通 metadata fixture 和完整单 Agent workflow。
- **Why this task exists**: 以一个具体能力验证 runtime_ref 到 workflow 的表达，不将示例名称变成平台内置类型。
- **Dependencies**: T05、T08。
- **Likely files/modules involved**: 已有 `yaml_instance/general_problem_solving_team.yaml` 的 Information Searcher（素材参考）；建议新增 `yaml_instance/` 薄 workflow、对应 manifest 项和 metadata fixture、注册/执行测试。
- **Implementation requirements**: 设计 §5、§20；整理已存在 prompt/tool 职责，去除对团队上游部门的依赖；元数据描述真实能力边界，runtime_ref 绑定完整静态图；注册分配 UUID/version。用 mock provider 验证输入到最终输出，不宣称真实检索成功。
- **Do-not-touch boundaries**: 不替换原团队 YAML、不实现六类 executor、不改 Router；不把真实 API key 写入 fixture，不启动未授权外部工具。
- **Acceptance criteria**: validator 接受恰一个业务 Agent 的 Research fixture；可通过 Registry 注册且引用可解析；mock provider 执行得到可确定最终输出。
- **Required tests**: manifest/薄图校验、注册成功、任务输入映射、mock provider 执行/最终输出；断言无隐藏多 Agent 或真实网络依赖。
- **Completion evidence**: metadata/runtime_ref/UUID-version 关联和脱敏 mock 执行证据；运行任务测试 → Registry/resolver 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T32 — 其余五类 demo fixtures 与完整目录配置

- **Task ID**: T32
- **Priority**: P0
- **Goal**: 补齐 Code/Data/Document/Planning/Review，与 Research 构成连贯的企业研发 / 知识工作 Agent 目录（enterprise R&D / knowledge-work Agent catalog），可重复登记。
- **Why this task exists**: 用企业调研、研发、分析与质量检查的连贯工作场景表达平台价值，并为 Demo/benchmark 提供能力边界和真实 UUID/version 映射；六项都是普通 fixtures，不是平台支持类型。
- **Dependencies**: T31。
- **Likely files/modules involved**: 已有 `yaml_instance/ChatDev_v1.yaml`、`data_visualization_basic.yaml`、`deep_research_v1.yaml`、`general_problem_solving_team.yaml`（素材参考）；建议新增其余薄 workflows/metadata/allowlist 与可重复注册入口及测试。
- **Implementation requirements**: 设计 §20、§21；每项 fixture（含 T31 ResearchAgent）须提供业务名称、清晰业务描述、capabilities、能力边界、tags、适用时的 tools、runtime_ref 和完整单 Agent thin workflow；能力边界在 description/fixture 说明中表达，不新增 metadata schema 字段。ResearchAgent 对应技术调研/比较/外部信息收集，CodeAgent 对应源码分析/调试/实现解释，DataAgent 对应结构化业务或工程数据分析，DocumentAgent 对应技术文档摘要/抽取/比较，PlanningAgent 对应项目拆解/里程碑规划/优先级排序，ReviewAgent 对应设计/输出/代码审查式质量检查。能力有意保留合理重叠，为 ambiguous routing 提供真实意义；不以标签迎合路由成绩。移除原团队依赖，逐个校验、mock 执行；登记流程可重复且输出 UUID/version 映射供数据集绑定；用临时第七个兼容 Agent 验证扩展。
- **Do-not-touch boundaries**: 不把 Data Analyst 名称直接视为通用 DataAgent，不建名称分支或 executor subclass，不改已有团队 YAML；不把 fixture UUID 当固定平台类型。
- **Acceptance criteria**: 六项上述内容齐全、业务职责连贯且有可解释的重叠/边界，均可校验、登记、mock 执行；重复登记不意外制造重复身份；添加第七个兼容 Agent 仅需普通 metadata、合格薄 workflow 与 allowlist 登记，不改 Router 源码，不硬编码六个名称。
- **Required tests**: 各 fixture 的内容完整性/结构/执行、完整目录登记及重复运行、UUID/version 映射有效性；人工核对业务工作负载和重叠边界。第七项扩展测试可用独立 fixture；Router 已就绪时复用验证其可被召回，尚未就绪时不提前实现它，路由行为证据在 T48 复核。
- **Completion evidence**: 六类登记映射、每个 mock 运行结果与第七项扩展证据；运行任务测试 → fixture/Registry 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

## Block C — Discovery & Routing

时间盒：约 3 天。先实现 fake backend 与独立语义路由，再接 rerank 和真实 embedding adapter。

### T06 — EmbeddingBackend 与 deterministic fake backend

- **Task ID**: T06
- **Priority**: P0
- **Goal**: 提供 provider-agnostic `EmbeddingBackend.embed(texts) -> vectors` 及可注入确定性向量实现。
- **Why this task exists**: Registry/Router 应能离线开发与复现测试，不能被真实 endpoint 或具体 SDK 绑定。
- **Dependencies**: T01。
- **Likely files/modules involved**: T01 业务包；建议新增 embedding 接口或置于 `server/services/agenthub/discovery.py`，fake backend 与单测放 `tests/`；现有 provider 代码只作风格参考。
- **Implementation requirements**: 设计 §8、§9、§22；明确批量输入输出顺序、model key 与维数信息，支持直接注入已知向量；校验非有限值、维数不一致、数量不匹配及不可计算 cosine 的零向量。fake 不访问网络、不模拟真实质量。
- **Do-not-touch boundaries**: 不安装本地模型、不绑定某厂商、不做索引/Router，不以 TF-IDF 或 fake 分数声称已交付真实语义效果。
- **Acceptance criteria**: 相同 fake 输入得到可重复向量，批量输出与输入一一对应；坏维数、NaN/Inf、零向量、数量不匹配有明确错误；接口可被后续真实实现替换。
- **Required tests**: 批量/空输入合约、对应顺序、重复调用确定性、维数/有限值/零向量校验和错误传播；断言零网络依赖。
- **Completion evidence**: 接口与确定性向量样例、测试命令/结果；运行任务测试 → metadata 相关回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T07 — Discovery 文本与版本化 embedding/index persistence

- **Task ID**: T07
- **Priority**: P0
- **Goal**: 生成规范公开能力文本，并持久化版本化向量及其来源信息。
- **Why this task exists**: 防止 metadata/向量错配、秘密进入 embedding 文本，保证 Registry 更新可原子验收。
- **Dependencies**: T02、T03、T06。
- **Likely files/modules involved**: AgentHub metadata/repository/embedding；建议扩展 `discovery.py` 与版本索引存取、对应纯函数及临时 DB 测试。
- **Implementation requirements**: 设计 §7–§9、§23；确定性拼接 name、description、capabilities、tags、可选 tools，能力短句为主；排除 role、YAML、runtime_ref、provider 配置和秘密。保存 `(agent_id, version, embedding_model_key, metadata_hash)`、维数与向量；所有语义字段变化导致 hash/索引重算；校验后以事务写入。
- **Do-not-touch boundaries**: 不读取附件正文、不复用 memory 索引作 Agent 目录、不引入向量服务；不提前实施 Top-K 或全目录模型切换。
- **Acceptance criteria**: 相同规范内容产生一致文本/hash；任一语义字段变更使新版本重算；持久向量与版本/model key/维数对应；失败无可见部分索引。
- **Required tests**: 所有语义字段逐项变化、非语义字段排除、公开文本脱敏、向量错误拒绝、批量对应、事务中断回滚、关闭重开后索引元数据一致。
- **Completion evidence**: 规范文本样例、字段覆盖和索引事务断言；运行任务测试 → metadata/repository/embedding 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T11 — Eligibility filtering 与 exact cosine Top-K

- **Task ID**: T11
- **Priority**: P0
- **Goal**: 从当前合格目录中精确计算 cosine 并返回稳定 Top-K 候选。
- **Why this task exists**: Router 需要与存储/模型实现解耦的候选接口，且停用、失效引用或不同向量空间不得混入排名。
- **Dependencies**: T05、T07、T10。
- **Likely files/modules involved**: `server/services/agenthub/discovery.py`（建议位置）、Registry/resolver/index；对应确定性向量测试。
- **Implementation requirements**: 设计 §8–§10；读取当前目录快照，过滤 disabled、无效 runtime_ref、不兼容 model key/维数/索引；精确扫描小目录，返回 `DiscoveryCandidate(agent_id, version, public_metadata, raw_similarity)`。同分按 agent_id/version 稳定排序；K 为配置输入，不冻结无依据值。
- **Do-not-touch boundaries**: 不做 ANN/向量数据库，不把筛选当工具授权，不调用 reranker/workflow，不加 demo 名称分支。
- **Acceptance criteria**: 已知向量得预期 cosine/顺序；空目录和少于 K 正确返回；改变输入顺序不改变 tie result；停用/失效条目及混合模型维数不能进入结果。
- **Required tests**: 可手算 cosine、空集、少于/等于/多于 K、同分随机输入顺序、disabled、引用失效、混合 model key/维数、状态切换后的新查询。
- **Completion evidence**: 候选/原始分数与过滤断言；运行任务测试 → Registry/index/resolver 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T12 — Semantic routing contract 与 confidence gate

- **Task ID**: T12
- **Priority**: P0
- **Goal**: 在不启动 Runtime 的条件下输出选中 Agent、拒绝或路由基础设施失败。
- **Why this task exists**: 无匹配与服务故障是不同业务结果，不能强选不相关 Agent 或把 cosine 当概率。
- **Dependencies**: T11。
- **Likely files/modules involved**: 建议新增 `server/services/agenthub/router.py`、路由结果 contract，复用 Discovery/EmbeddingBackend；独立 Router 测试。
- **Implementation requirements**: 设计 §10、§11、§16；semantic 选择稳定 Top-1；无候选或 Top-1 分数低于带来源阈值返回 `NO_SUITABLE_AGENT`。阈值相等不属于“低于”；阈值为带校准来源的配置输入。query embedding/检索错误返回基础设施失败，保留安全错误码与必要决策数据，不调用 Runtime。
- **Do-not-touch boundaries**: 不实现 TaskRun/API/LLM rerank、不硬编码无证据阈值、不静默 fallback；原始 score 不是概率置信度。
- **Acceptance criteria**: 可明确区分 selected/rejected/failed；边界分数行为符合冻结定义；query embedding 失败不变成 no-match，低分不强选；输出可供后续 trace 使用。
- **Required tests**: 无候选、阈值下/等于/上、同分选择、query embedding 失败、错误脱敏、原始 score 保留；断言零 workflow/provider 调用。
- **Completion evidence**: 三种结果及阈值边界样例、测试命令/结果；运行任务测试 → Discovery/Registry 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T13 — M1 独立垂直切片验收

- **Task ID**: T13
- **Priority**: P0
- **Goal**: 证明 Registry → Discovery → semantic 选择/拒绝在无 LLM/Runtime 环境下可完整复现。
- **Why this task exists**: 首个可用切片用于确认跨模块接口和剩余时间盒，不能只靠各模块孤立单测。
- **Dependencies**: T08、T09、T10、T11、T12。
- **Likely files/modules involved**: 上述业务服务；建议新增 `tests/` M1 集成用例、临时 DB/manifest/YAML fixtures，不新增生产职责。
- **Implementation requirements**: 设计 §22、§28；使用真实临时 SQLite、真实 resolver/validator、deterministic fake embedding，串联注册、发现、选择/拒绝、更新、禁用与重新打开数据库。测试 spy 保证没有 workflow/provider 执行；记录 M1 范围和未验证的 live 项。
- **Do-not-touch boundaries**: 不接 Task API、Run adapter、真实 embedding 或 UI，不以验收名义开始 M2；不得替换真实 repository/resolver 为全面 mocks。
- **Acceptance criteria**: 更新后新版本进入候选，禁用后不进入；无候选/低分拒绝；关闭重开目录和版本仍一致；整条路径不触发模型执行。
- **Required tests**: 一条或数条可重复 M1 跨层测试覆盖注册→选择、拒绝、更新→选择新版本、禁用→剔除、重开持久化，以及零 workflow/provider 调用。
- **Completion evidence**: M1 测试命令、逐场景结果及独立执行证明；运行任务测试 → T08–T12 相关回归 → 检查 `git diff` → 排除无关改动 → focused commit；复核剩余时间盒但不重新设计架构。

### T14 — LLM reranker contract 与调用适配

- **Task ID**: T14
- **Priority**: P0
- **Goal**: 实现只重排 Top-K 候选的可注入 reranker 输入输出边界。
- **Why this task exists**: LLM 只能在召回集合内排序，不能生成 Agent 或任意运行参数。
- **Dependencies**: T12。
- **Likely files/modules involved**: 建议在 AgentHub Router 相邻位置实现 reranker contract/transport adapter；现有 provider 调用方式只作参考；fake transport 测试。
- **Implementation requirements**: 设计 §10、§16、§23；输入仅 task 与 Top-K 的 ID/name/description/capabilities/tags；返回候选 ID 的排列或子集及短结构化理由码。严格校验未知/重复 ID、格式和选择可用性；不能产生可选首项的空结果不得当成功。超时/服务错误安全分类，不储存完整隐式推理链或秘密。
- **Do-not-touch boundaries**: 不向 LLM 发送整个目录、runtime_ref、YAML、role、凭据或附件正文；不直接执行所选 Agent、不修改 semantic 策略。
- **Acceptance criteria**: 合法排列/子集可解析且保持排序；未知/重复 ID、坏 JSON、无效格式、超时、服务错误明确失败；请求只含允许的公共字段。
- **Required tests**: fake transport 验证准确 payload、合法排列/子集、空/无效输出、重复/未知 ID、坏 JSON、超时和服务错误、敏感内容不回显。
- **Completion evidence**: 脱敏输入输出 contract 与反例测试结果；运行任务测试 → Router contract 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T15 — semantic_llm routing strategy

- **Task ID**: T15
- **Priority**: P0
- **Goal**: 在共享 semantic gate 后重排 Top-K，输出可追踪的第二种策略决策。
- **Why this task exists**: 两策略需要相同召回和拒绝基础，才可比较 rerank 的实际影响。
- **Dependencies**: T14。
- **Likely files/modules involved**: Router/Discovery/reranker 与策略测试；不依赖 Runtime/Run repository。
- **Implementation requirements**: 设计 §10、§11、§16；复用 semantic 召回/gate，无候选或低置信直接拒绝，只有通过 gate 才调用 reranker。选取合法返回第一名，保留原始召回分数和 rerank 顺序；任何 rerank 错误标 failed，不能悄悄切换 semantic。
- **Do-not-touch boundaries**: 不选候选外 Agent，不把 rerank 序位变成概率，不自动 retry/fallback、不调用 Workflow。
- **Acceptance criteria**: 低分不调用 LLM；合法重排可改变候选内选择而不改变原始分数；候选外/格式/调用错误失败；semantic 单独运行不需要 reranker。
- **Required tests**: gate 拒绝的零调用断言、合法重排、未知 ID 防线、超时/坏 JSON→failed、无静默 fallback、原始分数/顺序分别保留及 semantic 无 reranker 回归。
- **Completion evidence**: 两策略同输入的确定性输出与错误分类证据；运行任务测试 → semantic/reranker 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T16 — OpenAI-compatible embedding adapter

- **Task ID**: T16
- **Priority**: P0
- **Goal**: 为 provider-agnostic 接口接入独立配置的真实 embeddings 协议适配器。
- **Why this task exists**: fake 可验证工程行为，但 live integration/语义 benchmark 需要真实 endpoint。
- **Dependencies**: T06。
- **Likely files/modules involved**: AgentHub embedding adapter、现有配置/环境变量加载方式；建议补配置示例（无凭据）与 fake transport/显式 live 检查。
- **Implementation requirements**: 设计 §9、§23、§27；base URL、embedding model、凭据环境变量独立于聊天服务；校验批量返回索引/顺序、数量、维数、有限值与 model key。错误分类和日志脱敏，不能把 URL 中敏感片段或响应凭据输出；live 检查只在配置具备时显式执行。
- **Do-not-touch boundaries**: 不冻结具体厂商/model，不下载本地模型、不改既有聊天 provider、不把 live 检查放默认测试流程；不提交 `.env`。
- **Acceptance criteria**: fake transport 下协议输入/返回映射符合 T06；错误安全传播；配置缺失不影响离线测试。真实 endpoint 有配置时记录实测，否则明确 `NOT VERIFIED`，不能声称已验证 live。
- **Required tests**: 请求/响应映射、返回顺序变化、维数/数量/非有限值错误、认证/超时/服务失败分类、脱敏；另列 opt-in live embed 检查，不用 fake 结果代替。
- **Completion evidence**: fake transport 命令/结果、配置键说明和脱敏 live 结果或 NOT VERIFIED；运行任务测试 → embedding contract 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T17 — 受控全目录 re-embed 与索引切换

- **Task ID**: T17
- **Priority**: P0
- **Goal**: 模型或维度变更时重建完整目录索引，再切换有效 model key。
- **Why this task exists**: 不同向量空间不能混排，半成品迁移也不能破坏旧目录可用性。
- **Dependencies**: T07、T09、T16。
- **Likely files/modules involved**: embedding/index repository、Registry version switching；建议在同一业务包加入受控 re-embed 操作与临时 DB 测试。
- **Implementation requirements**: 设计 §9；对明确目录/版本快照构建新 model key 的向量，完成且校验一致后原子切换有效索引；中途失败保留旧索引。检测重建期间 metadata/version 变化，不能发布与当前版本错配的索引；受控重新构建或明确终止，不扩大成分布式迁移系统。
- **Do-not-touch boundaries**: 不改变 Agent 内容版本语义，不混合模型/维数，不删除仍有效旧索引，不实现 ANN 或自动后台调度。
- **Acceptance criteria**: 成功切换后候选统一来自新空间；任何中途失败或版本不一致不会留下半新半旧的有效目录；旧索引仍可用。
- **Required tests**: fake backend 模拟模型/维数改变、完整成功、部分失败、提交失败、重建中 Agent 更新、目录一致性及新旧空间绝不混排。
- **Completion evidence**: 切换前后 model key/版本快照和失败保留证明；运行任务测试 → Registry/index/embedding 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

## Block D — Run / Workflow Integration

时间盒：约 4 天。业务状态持久化在应用层；Runtime 仅接收默认关闭的可选 outcome/correlation 上下文。

### T18 — TaskRun persistence

- **Task ID**: T18
- **Priority**: P0
- **Goal**: 为每次平台提交持久化独立于 session 的业务 TaskRun。
- **Why this task exists**: 路由拒绝或启动前失败也必须留存，不能依赖内存 session 和成功执行归档。
- **Dependencies**: T02。
- **Likely files/modules involved**: AgentHub repository；建议在 `server/services/agenthub/` 定义 TaskRun 数据合约/存取，新增临时数据库测试。
- **Implementation requirements**: 设计 §6、§7、§14、§23；UUID run_id、任务文本、附件 ID 引用、策略、pending 等业务状态、UTC 时间、安全错误与结果引用；约束 ID/必需字段。大结果不入库；session 只能作为关联，不替代 run_id。状态机编排留给 T21。
- **Do-not-touch boundaries**: 不持久化线程/Future/executor，不改 native SessionStatus，不实现查询 API、Runtime 续跑或完整 Run history。
- **Acceptance criteria**: 写入后可按 run_id 查询；相同 session 的不同提交可有独立 run；数据库重开保留数据；未知 ID/非法字段明确处理。
- **Required tests**: 临时 DB 创建/读取/重开、未知 ID、唯一/必需字段约束、六种业务状态值范围、run_id 与 session_id 分离、附件仅存引用。
- **Completion evidence**: 脱敏记录与重开查询证据；运行任务测试 → repository 基础回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T19 — RoutingTrace persistence

- **Task ID**: T19
- **Priority**: P0
- **Goal**: 保存每次 TaskRun 的最终结构化路由 trace，包括失败尝试。
- **Why this task exists**: 只存最终 Agent 无法解释拒绝、定位基础设施错误或复现评测。
- **Dependencies**: T12、T18。
- **Likely files/modules involved**: AgentHub Router contract、TaskRun repository；建议新增 trace model/storage 与测试。
- **Implementation requirements**: 设计 §7、§16；每 run 至多一条最终 trace；保存 strategy、eligible 快照、候选 ID/version/raw cosine、threshold 值及来源、选中或拒绝、model key、可选 rerank 输入/排序/理由、错误码和 monotonic 毫秒耗时。rerank 数据结构可先用 fixture 验证，不增加 T15 隐含依赖；selected 与 rejection reason 互斥，基础设施失败可没有两者。
- **Do-not-touch boundaries**: 不存完整请求配置/隐式推理链、YAML、role、秘密、附件正文；不暴露原始向量，不要求每次中间排序都写一条最终 trace。
- **Acceptance criteria**: selected/rejected/failed 均可持久化可解释 trace；互斥字段受校验；重复通知不产生多条最终记录；延迟单位明确为 ms。
- **Required tests**: 三类结果、失败 trace、selected/rejection 互斥、唯一性/幂等、rerank nullable 数据、时间单位、重开查询与敏感字段排除。
- **Completion evidence**: 三类脱敏 trace 与约束断言；运行任务测试 → Router/TaskRun 持久化回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T20 — AgentRun persistence

- **Task ID**: T20
- **Priority**: P0
- **Goal**: 保存一次选定业务 Agent 执行与 ChatDev workflow/session/node 的关联快照。
- **Why this task exists**: Agent 版本、session 和节点调用不是同一身份，后续更新不能改变历史执行含义。
- **Dependencies**: T03、T18。
- **Likely files/modules involved**: AgentHub Agent version/TaskRun repository；建议新增 AgentRun model/storage 和持久化测试；`utils/token_tracker.py` 仅作 usage 来源参考。
- **Implementation requirements**: 设计 §7、§17；每 TaskRun 至多一条 AgentRun，固定 agent_id/version、runtime_ref 逻辑快照、session/workflow identity/revision/node、业务/native status、UTC 时间与 latency_ms、结果引用、安全错误和 nullable usage。没有 usage 必须 null/unknown；拒绝不创建 AgentRun，循环节点不逐次创建业务记录。
- **Do-not-touch boundaries**: 不复制完整日志/大产物，不存绝对路径/秘密，不将 node invocation 或 session 当业务 run，不做动态 team。
- **Acceptance criteria**: 单 run 唯一执行记录；版本更新/启停后历史快照不变；native status 独立保留；已知零 usage 与未知 usage 可区分。
- **Required tests**: 唯一约束、版本关联、目录变更后的快照稳定、关闭重开、nullable usage、结果引用和关联字段 round-trip；拒绝场景无 AgentRun。
- **Completion evidence**: 版本快照和关联查询样例；运行任务测试 → Agent/TaskRun repository 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T21 — Run transitions 与幂等终态

- **Task ID**: T21
- **Priority**: P0
- **Goal**: 实现冻结的六态业务投影与首次终态原子提交。
- **Why this task exists**: 重复通知、重连和完成/取消竞态不能覆盖已经确认的结果。
- **Dependencies**: T18、T19、T20。
- **Likely files/modules involved**: 建议 `server/services/agenthub/task_service.py` 的状态逻辑及 repository 条件更新；已有 `server/services/session_store.py` 只作 native 状态参考。
- **Implementation requirements**: 设计 §14、§15；允许 pending→running/rejected/failed/cancelled 和 running→success/failed/cancelled；终态不可逆。首次观察终态只提交一次，相关 TaskRun/AgentRun 数据保持一致；waiting_for_input 业务投影仍 running，native 原值保留。success 的 outcome 证据由后续 adapter 提供，此处不以 completed 推导成功。
- **Do-not-touch boundaries**: 不替换 native enum、不改 session 生命周期、不增加大型自定义状态机或重启续跑，不提前接 Runtime hooks。
- **Acceptance criteria**: 合法转换成功、非法转换被拒；重复通知幂等，完成/取消并发只产生一个终态；rejected/failed/cancelled 始终可区分。
- **Required tests**: 全部合法/非法转换、重复终态、两种到达顺序和受控竞态、事务失败、waiting_for_input/native completed 不自动 success、业务记录一致性。
- **Completion evidence**: 状态矩阵与竞态测试结果；运行任务测试 → Run/Trace 持久化回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T22 — Registry API

- **Task ID**: T22
- **Priority**: P0
- **Goal**: 通过最小 HTTP 接口暴露目录注册、更新、查询、启停。
- **Why this task exists**: 已验证的 Registry 服务需要应用入口，不应通过改写 workflow 请求混入目录操作。
- **Dependencies**: T08、T09、T10。
- **Likely files/modules involved**: 已有 `server/routes/__init__.py`、`server/models.py`（兼容性参考）；建议新增 `server/routes/agenthub.py` 及独立 request/response model、窄 API 测试。
- **Implementation requirements**: 设计 §4、§12、§23、§24、§26；复用 ALL_ROUTERS 注册方式，用 collection/item 与 enable/disable action 映射服务；具体路由按源码命名核对，不改变服务语义。校验失败/未知 ID/索引或引用失败提供安全错误，不回显物理路径/配置。
- **Do-not-touch boundaries**: 不修改现有 WorkflowRequest/WorkflowRunRequest 字段和验证，不加硬删除/审批/RBAC，不实现 Task API 或 Agent 管理页面。
- **Acceptance criteria**: Register/Get/List/Search/Update/Enable/Disable 可通过 HTTP 使用；异常不会留下部分注册/切换；公开响应不泄露敏感字段，旧 workflow 请求仍按原合约处理。
- **Required tests**: API 参数校验、未知 ID、分页/状态/搜索、更新版本、启停与失败响应；注入 fake embedding 和临时 DB；旧 workflow request model 的窄兼容检查。
- **Completion evidence**: 脱敏请求响应与 API 测试命令/结果；运行任务测试 → Registry/旧请求模型回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T23 — Task submission API 与运行前编排

- **Task ID**: T23
- **Priority**: P0
- **Goal**: 接受 task/session/attachment refs，记录 pending 和路由结果，并通过可注入执行入口派发。
- **Why this task exists**: 自然语言提交需要独立业务入口，拒绝/失败必须在 Runtime 之前可查询。
- **Dependencies**: T15、T19、T21。
- **Likely files/modules involved**: 建议 AgentHub routes/request model/task_service；已有 `server/routes/execute.py`、`uploads.py`、session/attachment 服务（读取复用合约）；fake execution adapter/API 测试。
- **Implementation requirements**: 设计 §12、§13、§23；校验已建立 session 与附件归属/有效性；合法提交创建 pending→调用策略→保存 trace。rejected 不创建 AgentRun/执行；路由基础设施错误 failed；selected 固定版本并经可注入入口派发，返回 run_id/session_id/摘要。执行接口先用 fake，真实 WorkflowRunService 接入属于 T28；提交响应不是执行成功。
- **Do-not-touch boundaries**: 不接受 yaml_file/任意路径，不读取附件正文做 embedding/rerank，不改旧 execute endpoint、不直接 new provider/GraphExecutor。
- **Acceptance criteria**: selected/rejected/failed 三条路径均持久化合理结果；只有 selected 派发一次；非法 session/跨 session 附件被拒；同步响应不会报告尚未完成的 success。
- **Required tests**: fake adapter 的选中/拒绝/失败与调用次数；缺失/非法 session、过期/跨 session 附件、YAML/path 字段拒绝、run_id 与 session 分离、请求校验与异步语义。
- **Completion evidence**: API 与存储状态对照、fake 派发断言；运行任务测试 → Router/Run/附件合约回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T24 — Run/trace query API

- **Task ID**: T24
- **Priority**: P0
- **Goal**: 按 run_id 返回业务状态、公开 trace 和结果引用，支持查询恢复。
- **Why this task exists**: WS 控制流事件不足以表达业务终态，进程内 session 也不是长期查询来源。
- **Dependencies**: T19、T20、T21。
- **Likely files/modules involved**: 建议 AgentHub query routes/response model，Run/Trace repository；已有 artifact/download API 只作引用约束参考。
- **Implementation requirements**: 设计 §12、§16、§17、§19；以独立 run_id 读取业务/native status、必要候选与拒绝/错误、result/artifact refs；关闭重开 DB 后仍可查询。仅公开必要字段，不返回原始向量、私密配置、绝对路径或堆栈。
- **Do-not-touch boundaries**: 不增加完整 Run list/history UI，不复制产物下载机制，不承诺重启续跑或恢复内存 session。
- **Acceptance criteria**: 已有 run 可查且状态/trace 一致；未知 ID 返回明确不存在；DB 重开查询成功；敏感字段不外泄。
- **Required tests**: selected/failed/rejected/cancelled 查询、未知 run、重开数据库、响应字段白名单、结果引用格式与安全错误响应。
- **Completion evidence**: 脱敏查询响应与持久查询证据；运行任务测试 → Run/Trace repository 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T25 — AgentExecutionOutcome contract 与 recorder

- **Task ID**: T25
- **Priority**: P0
- **Goal**: 定义轻量 succeeded/failed outcome 和每次运行隔离的 recorder。
- **Why this task exists**: provider 异常可能变成普通 Message；结构化执行事实必须独立于可见错误字符串。
- **Dependencies**: T01、T21。
- **Likely files/modules involved**: 已有 `runtime/node/executor/base.py`（上下文参考）、`entity/messages.py`（保持不变）；建议 runtime 可用且不依赖 AgentHub repository 的小型 outcome contract/recorder 与单测。
- **Implementation requirements**: 设计 §15；记录 run_id、目标 node_id、succeeded/failed、安全 error_code/短类别、可选安全摘要。recorder 生命周期限当前 run，可判定缺失、重复和矛盾记录；为 adapter 提供明确 invalid/missing 结果，不默认成功、不解析 Message。
- **Do-not-touch boundaries**: 不修改 Runtime 行为、旧 Message/native status 或全局异常传播；此项只定义 contract/recorder，不实施上下文传递（T26）或节点打点（T27）。
- **Acceptance criteria**: 成功和失败可结构化读取；错误不含凭据；重复一致与矛盾输入有明确处理，缺失不伪造成功；不同 run/节点不串数据。
- **Required tests**: success/failure、缺失、重复一致/冲突、错误码与脱敏、跨 run/目标 node 隔离；无 DB/provider 依赖。
- **Completion evidence**: outcome 样例、recorder 边界和隔离测试；运行任务测试 → 状态 contract 相关回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T26 — 可选 outcome context 传递 — HIGH RISK

- **Task ID**: T26
- **Priority**: P0 — HIGH RISK
- **Goal**: 将可选 run correlation/recorder 贯穿实际 ExecutionContext 组装链。
- **Why this task exists**: graph metadata 会重建，不能依赖它碰巧保留业务关联；需要明确旁路上下文。
- **Dependencies**: T25。
- **Likely files/modules involved**: 已有 `runtime/node/executor/base.py::ExecutionContext`、`workflow/graph.py`、`workflow/runtime/runtime_context.py` / `runtime_builder.py`（按真实组装链确定必要点）；上下文测试。
- **Implementation requirements**: 设计 §13、§15、§24；沿实际构造/传递位置增加默认关闭的可选上下文，注入时 run/node 可关联，未注入时保持原行为。Runtime 只依赖通用 recorder contract，不导入 AgentHub repository/service。
- **Do-not-touch boundaries**: 不重写 DAG/parallel/cycle 调度、GraphManager metadata 重建或 Message；不增加业务发现到 Runtime，不把可选参数变必填。
- **Acceptance criteria**: 原构造和默认执行仍有效；注入 recorder 可到达目标执行上下文，跨 run 不泄漏；不经易被覆盖的 graph metadata 传递。
- **Required tests**: 旧 ExecutionContext/graph 构造、无 recorder 执行、注入后的关联传播、两个 run 隔离和受影响图路径回归。
- **Rollback boundary**: 回退本项新增可选 recorder/context 传递链；保持 T25 独立 contract 与既有持久数据，不改旧 workflow 配置。
- **Targeted regression**: 无 recorder 的 Message/上下文默认值、已有 graph 构造与调用方式、运行间隔离。
- **Minimum-change requirement**: 只添加必要可选字段/传递参数，默认路径不改变；不重写异常传播、图调度或 WS 协议。
- **Completion evidence**: 默认/注入路径对照、最小 diff 和可回退范围；运行任务测试 → targeted regression → 检查 `git diff` → 排除无关改动 → focused commit。

### T27 — AgentNodeExecutor 成功/失败 outcome — HIGH RISK

- **Task ID**: T27
- **Priority**: P0 — HIGH RISK
- **Goal**: 在真实 Agent 执行成功和捕获异常边界记录结构化 outcome。
- **Why this task exists**: 必须捕获已观察到的 provider 失败→assistant 错误文本路径，同时保留上游行为。
- **Dependencies**: T26。
- **Likely files/modules involved**: 已有 `runtime/node/executor/agent_executor.py::AgentNodeExecutor.execute()`、provider create/call 边界；T25 recorder 与 mock provider 测试。
- **Implementation requirements**: 设计 §15、§23、§24；在实际成功路径记录 succeeded，在 provider 创建/调用等捕获执行异常边界记录 failed；只在 recorder 存在时记录，保留原 Message、旧错误文本和原异常处理行为。新的 outcome/error summary 必须脱敏，不能复制异常原文中秘密。
- **Do-not-touch boundaries**: 不改返回类型/旧错误文本合约，不重写 Runtime 异常传播、provider retry/tools/memory；不将字符串启发式用于判定 outcome。
- **Acceptance criteria**: mock provider 成功/创建失败/调用失败均产生正确结构化状态；旧 Message 仍按原路径返回；无 recorder 行为与之前一致；新公开 outcome 不含秘密。
- **Required tests**: mock provider 成功、create_client 抛错、调用抛错、含合成敏感值的异常脱敏、无 recorder 的 Message/error path、目标 run/node 关联。
- **Rollback boundary**: 回退本项 AgentNodeExecutor 的可选 recorder 写入点，保留旧 Message/错误输出路径；不更改 AgentHub 数据库。
- **Targeted regression**: 无 recorder 的成功与错误 Message、默认 provider 调用路径及相关 Agent executor 单测。
- **Minimum-change requirement**: 仅在已核实的成功/失败边界旁路记录，不调整 catch 范围或将吞错路径改为抛错，不改图完成语义。
- **Completion evidence**: 三类 provider 路径的 outcome/旧 Message 对照和脱敏断言；运行任务测试 → targeted regression → 检查 `git diff` → 排除无关改动 → focused commit。

### T28 — WorkflowRunService adapter 与 run/session correlation — HIGH RISK

- **Task ID**: T28
- **Priority**: P0 — HIGH RISK
- **Goal**: 将选定 Agent 通过原 Web 执行链启动，并传递可选 run/session/outcome 关联。
- **Why this task exists**: 绕过 WorkflowRunService 会丢失 WebSocket、附件、human input 和取消生命周期。
- **Dependencies**: T05、T20、T23、T26。
- **Likely files/modules involved**: 建议 AgentHub task/execution adapter；已有 `server/services/workflow_run_service.py`、`server/services/websocket_executor.py`、session/attachment 服务及相关集成测试。
- **Implementation requirements**: 设计 §5、§12、§13、§17、§24；执行前重新解析 allowlist 并校验目标存在与版本一致；沿 `WorkflowRunService.start_workflow()`→WebSocketGraphExecutor 传递可选 recorder/correlation，run_id 不覆盖 session_id。Web 附件继续传 ID；固定 AgentRun 快照；引用/启动失败安全持久化且不伪装 rejected。
- **Do-not-touch boundaries**: 不直接调用 provider/SDK GraphExecutor 跳过 Web 路径，不改变原调用者必需参数，不替换 WS/session/upload 协议或附件 ID 语义。
- **Acceptance criteria**: 正确引用启动原执行链并可追溯 run/session/node；选中后引用失效阻止 Runtime 且业务 failed；启动异常留存；原默认调用签名与行为可继续使用。
- **Required tests**: 真实服务路径配 mock provider/fake可控执行边界；引用失效/移除/校验变化、启动失败、可选上下文关联、附件 ID 传递、旧 start_workflow/execute 调用回归。
- **Rollback boundary**: 回退 AgentHub adapter 与本项新增可选 hooks/参数；保留原 WorkflowRunService 默认入口、历史 Run 数据和已有附件。
- **Targeted regression**: 原 `/api/workflow/execute`、默认 WorkflowRunService、WS/session 创建、附件传递及 human input/cancel 接口。
- **Minimum-change requirement**: 仅为显式 AgentHub 调用增加必要关联；不重写线程池、跨 loop 消息发送、WS 协议或图执行链。
- **Completion evidence**: run/session/workflow 对应、启动失败和旧调用证据；运行任务测试 → targeted regression → 检查 `git diff` → 排除无关改动 → focused commit。

### T29 — Workflow completion 到业务终态适配 — HIGH RISK

- **Task ID**: T29
- **Priority**: P0 — HIGH RISK
- **Goal**: 用正常控制流结束与结构化 Agent outcome 共同决定业务终态。
- **Why this task exists**: `workflow_completed` 单独不足以证明 Agent 执行成功，直接映射会高估成功率。
- **Dependencies**: T27、T28、T21。
- **Likely files/modules involved**: AgentHub execution adapter/task_service、outcome recorder、Run repository；已有 WorkflowRunService 完成边界及必要可选 hooks、集成测试。
- **Implementation requirements**: 设计 §14、§15、§17；只有 workflow 正常结束、唯一目标业务 node outcome=succeeded 且无异常/取消才 success。failed outcome→failed；目标未达/缺失/矛盾 outcome 使用 execution_outcome_missing/invalid 类明确失败码。native status 原样保存，终态幂等，结果与 usage 使用已知来源。
- **Do-not-touch boundaries**: 不解析 Message 文本判错，不改原 workflow_completed 或 ResultArchiver 成功事件，不把 execution success 当答案质量，不覆盖先到终态。
- **Acceptance criteria**: 正常成功成立；必须复现 provider failure + workflow completion event → TaskRun.failed，native completed 可并存；缺失/矛盾 outcome 不误成功，重复完成不会改写终态。
- **Required tests**: 真实 WorkflowRunService/Runtime 链配 mock provider，覆盖成功、provider 创建/调用失败但 completed、缺失/矛盾/错误 node outcome、重复完成、已取消终态不被晚到成功覆盖。
- **Rollback boundary**: 回退 AgentHub 完成适配/hooks；保留原 workflow 事件、日志和持久历史，不迁移或改写旧终态记录。
- **Targeted regression**: 普通 workflow completed/WS/Message 行为、默认 WorkflowRunService、无 recorder 路径与 TaskRun 幂等。
- **Minimum-change requirement**: 业务投影留在 adapter，默认路径不改变；不重写全局异常传播或按文字推断执行结果。
- **Completion evidence**: provider failure 仍 completed 而业务 failed 的脱敏集成证据、成功/缺失对照；运行任务测试 → targeted regression → 检查 `git diff` → 排除无关改动 → focused commit。

### T30 — Workflow exception 与 cancellation

- **Task ID**: T30
- **Priority**: P0
- **Goal**: 完整覆盖启动/图异常、协作取消、断线重连与晚到事件的业务投影。
- **Why this task exists**: 取消请求、WS 断开和实际执行结束是不同事件，不能提前宣称停止或覆盖已完成结果。
- **Dependencies**: T29。
- **Likely files/modules involved**: AgentHub execution adapter/state service；已有 WorkflowRunService、session controller/store、WS cancel 路径（最小复用）；集成测试。
- **Implementation requirements**: 设计 §13–§15；图/启动异常→failed；取消沿已有协作机制，仅在实际执行结束或 cancellation boundary 确认后提交 cancelled。断 WS 不自动取消；保留 native 状态；完成、取消和晚到事件遵循首次终态。重连/查询恢复不承诺服务重启续跑。
- **Do-not-touch boundaries**: 不强杀同步 provider/tool、不改变线程/事件循环架构、不新增 fallback/retry，不把取消计入拒绝或覆盖先到 success。
- **Acceptance criteria**: 图异常有持久 failed；确认取消为 cancelled；同步调用尚未结束时不提前报告取消完成；断线不改变业务终态；重连和晚到消息不能覆盖首次终态。
- **Required tests**: 启动/图异常、pending/运行中取消、可控同步 provider 阻塞与释放、WS 断开/重连、完成先到/取消先到及重复晚到事件、持久记录一致性。
- **Completion evidence**: 有界并发测试与脱敏事件/状态时间序列；运行任务测试 → outcome/Run 幂等/原取消路径回归 → 检查 `git diff` → 排除无关改动 → focused commit。

## Block E — UI & Observability

时间盒：约 2 天，其中 T49 约半天至一天。复用 Vue 样式、API helpers 和 Launch/session/upload/WS/结果组件，提供最小业务入口、Registry 管理与指标；压缩视觉打磨和可选展示，不扩建控制台。

### T33 — Metrics aggregation 与最小 API

- **Task ID**: T33
- **Priority**: P0
- **Goal**: 从 SQLite 业务事实聚合轻量运行指标并提供最小读 API。
- **Why this task exists**: native completed 和日志时长不能直接作为平台成功率或执行耗时。
- **Dependencies**: T19、T20、T21、T29、T30。
- **Likely files/modules involved**: AgentHub Run/Trace repository、新 metrics 查询/API；已有 `utils/token_tracker.py`、`utils/logger.py`、`utils/log_manager.py` 只作明细来源参考；固定数据测试。
- **Implementation requirements**: 设计 §17、§18；Total Runs 含所有已接受任务；Execution Success/Failure Rate 只用有结构化 outcome 的已执行 succeeded/failed 集合，取消/拒绝单列；显示分母。汇总 routing/execution latency_ms（执行 wall-clock 含人工等待）、token 已知值及 unknown 数、Agent ID/version usage、strategy/selected/rejection distribution；质量指标无评价则未测量。
- **Do-not-touch boundaries**: 不部署监控栈、不把未知 usage 当 0、不相加重复嵌套日志时长、不推断任务质量、不扩展完整 Dashboard。
- **Acceptance criteria**: 手工固定数据的计数/分母/延迟与 API 一致；空样本不伪造 0%；拒绝/取消不混入执行失败率；unknown 独立可见。
- **Required tests**: 临时 SQLite 固定混合状态、缺失 outcome、已知零/未知 tokens、零样本、ms 单位、含人类等待延迟、Agent/version 和 strategy 分组、质量未测量响应。
- **Completion evidence**: 手算期望与 API 实际值、数据来源/分母说明；运行任务测试 → Run/Trace/终态回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T34 — Frontend AgentHub task-entry mode — HIGH RISK

- **Task ID**: T34
- **Priority**: P0 — HIGH RISK
- **Goal**: 在保留手选 YAML 的前提下加入最小自然语言 AgentHub 提交模式。
- **Why this task exists**: 当前 Launch 依赖先选 workflow，平台入口需要独立任务请求并复用既有会话/附件。
- **Dependencies**: T23、T28。
- **Likely files/modules involved**: 已有 `frontend/src/pages/LaunchView.vue`、`frontend/src/utils/apiFunctions.js`、必要的 router/复用组件；必要时一个薄入口，增加窄范围前端测试支持。
- **Implementation requirements**: 设计 §12、§19、§24；task 输入、session upload refs、strategy 默认 semantic；WS 就绪后提交业务请求并保存 run_id/session_id；沿用附件上传。保留手动 YAML 原请求体/条件；测试支持只覆盖本变更必需交互，不全面改造前端工具链。
- **Do-not-touch boundaries**: 不重写 Launch/WS 协议，不改 workflow 编辑器/schema/画布保存，不加 Agent 管理控制台或完整 history；不把 API accepted 显示为 success。
- **Acceptance criteria**: AgentHub 模式发送 task/session/attachments/strategy 而非 yaml_file；手动模式保持原 yaml_file/task_prompt 请求；未就绪不能提交，附件归属正确。
- **Required tests**: mock API/WS 的两种模式请求体、默认 semantic、连接就绪条件、上传附件 ID、提交失败；原手选 YAML 基础流程；相关前端 build 检查。
- **Rollback boundary**: 回退新增模式/薄入口及 AgentHub API helper，保留原 Launch、附件上传和 workflow 页面。
- **Targeted regression**: 手动 Launch、YAML 选择、旧 execute 请求、WS 就绪与附件传递。
- **Minimum-change requirement**: 复用现有组件和函数，只增必要分支；不重写 Launch，不全局重构状态或升级无关依赖。
- **Completion evidence**: 新旧请求体测试、交互与构建结果及必要脱敏界面证据；运行任务测试 → targeted regression → 检查 `git diff` → 排除无关改动 → focused commit。

### T35 — 候选、选中 Agent 与拒绝展示

- **Task ID**: T35
- **Priority**: P0
- **Goal**: 展示可理解的有序候选、选中 Agent 和拒绝/路由失败理由。
- **Why this task exists**: 用户需要看见选择依据和拒绝原因，同时不能误把 raw cosine 视为可靠概率。
- **Dependencies**: T34、T24。
- **Likely files/modules involved**: AgentHub Launch 模式/薄入口、API helper、必要小组件和 mock API 测试。
- **Implementation requirements**: 设计 §16、§19；按 trace 顺序呈现 Agent ID/version 与公开名称、raw score/score kind、选中项或 NO_SUITABLE_AGENT 理由；使用 rerank 时区分召回分数和重排序位；基础设施失败有独立状态。只展示公开查询数据。
- **Do-not-touch boundaries**: 不显示 provider 配置/物理路径/原始向量，不把 score 转百分比概率，不改 Router/gate 或扩展管理 UI。
- **Acceptance criteria**: selected/rejected/failed 各有准确展示；候选顺序与 API 一致，拒绝不出现伪选中；raw cosine 标签清楚，空候选可读。
- **Required tests**: mock API 的选中/低分拒绝/无候选/基础设施失败、候选顺序、rerank 顺序区分、分数文案不含概率误导、缺失字段安全显示。
- **Completion evidence**: 三类响应的组件断言和脱敏界面样例；运行任务测试 → 任务入口/API helper 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T36 — 业务 Run-state、结果与查询恢复 — HIGH RISK

- **Task ID**: T36
- **Priority**: P0 — HIGH RISK
- **Goal**: 在原 WS 输出/产物流旁独立显示业务状态，并以 run_id 查询恢复。
- **Why this task exists**: UI 不能因 WS completed 覆盖业务 failed，刷新也不能丢失可查询的业务终态。
- **Dependencies**: T35、T29、T30。
- **Likely files/modules involved**: `frontend/src/pages/LaunchView.vue` 或其薄 AgentHub 入口、API helper、已有输出/附件/human input/download 组件与测试。
- **Implementation requirements**: 设计 §13–§15、§19、§24；run_id 可用于 URL state/查询恢复；业务状态由业务查询/结果决定，native WS 日志继续显示。复用原结果、产物和人工输入；取消按确认终态显示，刷新/重连保持关联但不声称重启续跑。
- **Do-not-touch boundaries**: 不替换 WS/native enum、原 human input/download 流程，不把 completed 直映 success，不增加复杂前端状态框架。
- **Acceptance criteria**: WS completed 与业务 failed 可同时正确表达；刷新/重连恢复相同 run；取消请求与取消完成有区分；普通 Launch 输出、human input、附件和下载仍工作。
- **Required tests**: mock API+WS 的 completed/failed 并存、pending/running/六态显示、刷新 URL/run 查询、重连/晚到事件、取消确认、原结果/human input/产物下载回归；相关构建。
- **Rollback boundary**: 回退新增业务状态投影和 run 查询恢复，保留原 WS 输出/产物流及历史 DB 记录。
- **Targeted regression**: 手动 Launch、附件/输出/结果、人工输入、取消、刷新重连和日志下载。
- **Minimum-change requirement**: 只增加独立业务投影及必要关联，不重写 Launch 消息处理或 WS 协议，不改变旧完成状态语义。
- **Completion evidence**: completed/failed 并存与恢复证据、新旧流程测试结果；运行任务测试 → targeted regression → 检查 `git diff` → 排除无关改动 → focused commit。

### T37 — 最小 metrics view

- **Task ID**: T37
- **Priority**: P0
- **Goal**: 在轻量视图中呈现真实计数、分母、延迟和 usage 可用性。
- **Why this task exists**: 指标只有说明样本和未知值才可解释，不能把未测量质量包装成成功率。
- **Dependencies**: T33、T36。
- **Likely files/modules involved**: 最小 AgentHub 视图/组件与 API helper、前端 mock metrics 测试。
- **Implementation requirements**: 设计 §18、§19；展示 Total Runs、业务分类、执行成功/失败分母、routing/execution ms、Agent usage/distribution、known/unknown usage；标明 execution latency 含人工等待，质量无数据时显示未测量。
- **Do-not-touch boundaries**: 不加 richer Dashboard、完整 history、实时监控栈或未定义指标，不改变后端统计口径。
- **Acceptance criteria**: 空数据不出现误导性 0%；混合状态的计数/分母与 API 一致；ms 单位、unknown tokens、未测量质量一目了然。
- **Required tests**: 空数据、混合终态、缺失 usage、零值 usage、分母显示、时间单位、未测量质量及 API 错误显示；相关组件/build 检查。
- **Completion evidence**: 固定数据的界面断言与脱敏截图/交互证据；运行任务测试 → task/run 视图回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T49 — Minimal Agent Registry Admin UI

- **Task ID**: T49
- **Priority**: P0
- **Goal**: 用约半天至一天交付最小管理员 Registry 界面，使平台具备可见的 Agent 登记与启停体验。
- **Why this task exists**: 管理员应能管理企业能力目录、查看能力/版本/状态，与业务用户的任务路由体验形成完整平台闭环。
- **Dependencies**: T22。
- **Likely files/modules involved**: 已有 Vue 应用、`frontend/src/utils/apiFunctions.js`、`frontend/src/router/index.js` 与页面/组件样式；建议增加一个薄 Registry 视图/表单及窄前端/API 测试，具体落点实施前核实。
- **Implementation requirements**: 复用 T22 Registry API 与现有 Vue 风格/API helpers；列表显示 Agent name、description、capabilities、version、status、runtime_ref 逻辑身份及 API 已提供的有效性/错误状态，不暴露路径、YAML 或 provider 配置。支持 Register、API 已支持的 metadata Update、Enable、Disable；服务端仍负责 runtime_ref/index 校验，UI 不复制校验引擎或制造未经验证的 ready 状态。操作成功刷新列表，安全显示字段校验/注册/更新/启停/API 错误和空目录。搜索/过滤仅在复用现有 API 几乎无额外成本时加入。现有 Vue/helper 已提供基础，不依赖 T34 的执行模式；若 T34 窄测试支持已存在则复用，不为此增加依赖。
- **Do-not-touch boundaries**: 不做复杂 Agent editor、visual workflow editor、RBAC、审批、Marketplace、部署管理、版本 diff viewer 或完整 Agent operations console；不接收任意 YAML/物理路径、不扩展 Registry API 职责、不重写旧页面或全局状态。不因本任务阻塞 M1 路由或 M2 Runtime 验收。
- **Acceptance criteria**: 管理员能注册合法 Agent 并在列表看到它，能力/version/status 清楚可见；能更新 API 支持的 metadata；禁用后 UI 显示 disabled；当前 runtime_ref/index 仍有效时可重新启用；注册/启停失败有安全错误提示且不显示伪成功；空目录可理解；现有 ChatDev Launch/Workflow 页面仍工作。
- **Required tests**: mock/narrow frontend/API tests 覆盖 list、register、validation failure、metadata update、enable、disable、API failure、empty catalog；包括无效 runtime_ref/index 导致启用失败与状态保持。运行相关前端 build、API helper/旧 Launch/Workflow 页面回归，保留交互验收证据。
- **Completion evidence**: 注册→列表→禁用→重新启用的脱敏 UI/测试证据、错误/空目录断言、构建及旧页面回归结果；任务测试 → 相关回归 → 完整 diff review → focused commit → push `origin/agenthub-dev` → 停止。

## Block F — Evaluation & Delivery

时间盒：约 2 天。复用各任务已有测试/构建证据及 runner，规模测量仅扩展配置与必要计时；路由评测独立于真实 workflow 执行，结果必须来自实际运行，不为赶工删减必需验证。

### T38 — Benchmark dataset schema 与 split 校验

- **Task ID**: T38
- **Priority**: P0
- **Goal**: 定义可标注、版本化且能隔离校准/测试数据的 routing case 合约。
- **Why this task exists**: 评测标签须绑定目录快照，并防止调阈值后用同一数据报告泛化成绩。
- **Dependencies**: T01。
- **Likely files/modules involved**: 建议独立 evaluation/dataset schema 与 validator 位置（在实现时按仓库风格落地），`tests/` schema fixtures；不要求预建完整评测框架。
- **Implementation requirements**: 设计 §20–§22；case_id、task_text、可选附件 fixture ref、expected_agent_ids 集合、should_reject、clear/ambiguous/no_match、标注依据、dataset version/split、目录快照/版本关联。校验唯一性、标签一致性、引用存在及 calibration/test 隔离；测试用合成目录，无需 T32 前置。
- **Do-not-touch boundaries**: 不填写 benchmark 分数、不复制生产任务/个人附件/秘密，不实现 runner 或阈值校准。
- **Acceptance criteria**: 合法 clear/ambiguous/no_match 样例通过；重复 ID、错误类别/标签组合、失效引用和 split 重叠被拒；可接受 Agent 集合表达明确。
- **Required tests**: schema 正反例、唯一性、空/重复期望集合、no-match 与 should_reject 一致性、Agent/version 快照引用、split 交叉和重复任务检查。
- **Completion evidence**: 数据合约与校验反例、测试命令/结果；运行任务测试 → metadata/schema 相关回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T39 — 36 clear cases

- **Task ID**: T39
- **Priority**: P0
- **Goal**: 创建六类各六条清晰能力路由样例及可解释标注。
- **Why this task exists**: 基准需要覆盖各能力的不同任务，不能用同句改写堆积虚假样本量。
- **Dependencies**: T32、T38。
- **Likely files/modules involved**: 建议 evaluation dataset 文件、目录 UUID/version snapshot 引用和 dataset 校验测试；不改生产 Router。
- **Implementation requirements**: 设计 §20、§21；总计 36 clear，六类各 6；每条说明能力适配依据，绑定实际注册 UUID/version 映射。任务文本有实质差异，标明 dataset version/split，不预填任何实测决策/耗时或成绩。
- **Do-not-touch boundaries**: 不扩大 Agent 能力以迎合标签，不实现 workflow 任务质量评分，不调 Router 阈值、不混入敏感真实任务。
- **Acceptance criteria**: schema 全通过；数量/类别分布准确；目录引用有效；人工检查确认标签解释成立且无同句改写堆积。
- **Required tests**: schema、36 总数/每类 6、case ID/文本重复、Agent/version 绑定和 split 隔离；人工语义审阅记录。
- **Completion evidence**: 数据校验命令/结果、分布与标注审阅记录；运行任务测试 → dataset validator 回归 → 检查 `git diff` → 排除无关改动 → focused commit，无 benchmark 结果声明。

### T40 — 12 ambiguous + 12 no-match cases

- **Task ID**: T40
- **Priority**: P0
- **Goal**: 补全模糊/跨能力和应拒绝案例，使完整数据集达到 60 条。
- **Why this task exists**: 只测清晰匹配无法验证可接受多答案、拒绝能力或阈值的误接受风险。
- **Dependencies**: T39。
- **Likely files/modules involved**: T39 dataset 与目录 snapshot、schema/分布测试。
- **Implementation requirements**: 设计 §11、§21；12 ambiguous 明确可接受 Agent 集合和重叠能力理由；12 no_match 标注 should_reject 及目录不适配理由。保留校准/独立测试 split，总体分布 36/12/12，不用测试集调阈值。
- **Do-not-touch boundaries**: 不把基础设施错误当 no-match，不强制模糊 case 只有一个答案，不填未运行成绩、不更改冻结分布。
- **Acceptance criteria**: 共 60 条且分布准确；全部 schema/引用有效；模糊集、拒绝理由与实际能力边界一致；split 不重叠。
- **Required tests**: 全量 schema、60 与 36/12/12 计数、重复检查、可接受集合/拒绝标签一致性、snapshot 引用、split 隔离；人工核对模糊与负例标注。
- **Completion evidence**: 全量数据校验与标注审阅记录；运行任务测试 → T38/T39 数据回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T41 — Routing benchmark runner

- **Task ID**: T41
- **Priority**: P0
- **Goal**: 在固定目录与路由配置下生成逐 case 决策及实测路由耗时。
- **Why this task exists**: 可复现比较需要独立 Router 测量，不应被实际 Agent workflow 的成本/成败混淆。
- **Dependencies**: T15、T38。
- **Likely files/modules involved**: 建议 evaluation runner/输出格式，复用两策略 Router 与 dataset validator；fake backend/reranker 测试。
- **Implementation requirements**: 设计 §21；固定 metadata snapshot、embedding model key、K、threshold/来源、strategy 和 dataset version/split。逐 case 保存 selected/rejected/infra error、semantic candidates、rerank 信息及 query embedding 到决策返回的实际 monotonic ms；记录环境。可用小合成数据测试，不依赖 T40 完成。
- **Do-not-touch boundaries**: 不启动 workflow/provider Agent 执行，不把 benchmark 做成端到端执行框架，不估算延迟或填假结果。
- **Acceptance criteria**: 同一 fake 配置产生可重复决策/排序；计时来自实测（耗时本身不要求字节相同）；每条输出可关联 case/config，基础设施错误不丢失。
- **Required tests**: fake embedding/reranker 两策略、固定输入的决策重现、配置 snapshot 输出、错误 case 留存、有界有效 ms、spy 断言零 workflow 调用。
- **Completion evidence**: 小样本逐 case 输出、配置 provenance 和无 workflow 断言；运行任务测试 → Router/dataset 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T42 — Routing benchmark metrics

- **Task ID**: T42
- **Priority**: P0
- **Goal**: 从逐 case 结果计算可核查的路由准确率、召回、拒绝指标和延迟。
- **Why this task exists**: 错误分母或把服务错误算拒绝会夸大效果，需先用手算数据验证。
- **Dependencies**: T41。
- **Likely files/modules involved**: evaluation metrics/runner report、小型手算 fixture 与单元测试。
- **Implementation requirements**: 设计 §21；Top-1 对 clear/ambiguous 检查 selected 是否在可接受集合；Top-K 检查 semantic 候选是否召回可接受 Agent。Reject Accuracy/False Accept Rate 用有效完成路由的 no-match 集合，infra error 单列；所有指标输出有效样本数/分母，错误样本与排除口径显式标注；分策略报告实测 latency。
- **Do-not-touch boundaries**: 不把基础设施失败当拒绝/误接受，不按 rerank 排序替代 semantic recall 集，不把路由指标等同 Execution/Task Quality Success。
- **Acceptance criteria**: 手算样例与程序结果完全一致；应路由 case 被拒不能算 Top-1 正确；no-match 选中任意 Agent 是 false accept；空分母未测量而非伪 0%。
- **Required tests**: 正确/错误选中、多可接受集合、Top-K 命中/缺失、错误拒绝、no-match 正确拒绝/误接受、infra error、空集合、延迟汇总与样本分母。
- **Completion evidence**: 手算依据、程序结果和错误/排除计数；运行任务测试 → runner/dataset 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T43 — 阈值校准与两策略实测比较

- **Task ID**: T43
- **Priority**: P0
- **Goal**: 使用真实配置服务在独立 split 上校准并比较 semantic 与 semantic_llm，补充小目录 exact cosine scan 的规模性能证据。
- **Why this task exists**: 阈值不能凭空冻结，两策略优劣必须来自同一可追溯配置下的实测。
- **Dependencies**: T16、T17、T40、T41、T42。
- **Likely files/modules involved**: dataset splits、runner/metrics、受控 benchmark 配置与结果文档/产物；真实凭据仅在忽略的环境配置中。
- **Implementation requirements**: 设计 §8–§11、§21、§27；保留 36 clear / 12 ambiguous / 12 no-match、Top-1、Top-K、Reject Accuracy、False Accept Rate。先验证真实 embedding 配置，仅在 calibration split 选 K/threshold，冻结后在独立 test split 比较 semantic 与 semantic_llm；保留模型/目录版本、配置来源、环境、逐 case 结果/延迟与错误样本，样本不足须说明。另以约 10/50/100 Agents 做轻量 catalog-size benchmark，必要时生成固定种子、受控且符合 metadata/runtime_ref/index contract 的 synthetic catalog；与 60-case 质量集分开。记录硬件、目录构造、向量维度/model key、K、策略、样本/重复次数及预热/缓存条件。分别报告 routing latency p50/p95，并在技术可行时分离 semantic retrieval latency（明确是否含过滤/加载）、query embedding latency、LLM rerank latency；目录建索引时间与每请求路由分开，无法分离的项注明未测及原因。区分 local deterministic engineering benchmark 与 live provider latency benchmark；前者用受控向量/fake 验证本地扫描成本，不能代表 live semantic-provider 延迟或质量。以实测讨论“小目录 exact cosine scan 足够、当前不需要专用向量数据库”的 P0 决策适用范围；不预设结论或目标，反向证据也如实记录，架构调整另行评审。
- **Do-not-touch boundaries**: 不用 test split 调优后报告泛化成绩，不虚构性能目标/缺失结果，不把 fake latency 冒充 live latency，不启动 Agent workflow；不为 benchmark 添加 Qdrant/FAISS 或其他基础设施，不提交凭据。
- **Acceptance criteria**: 校准→固定配置→独立测试可追溯，两策略共享召回/gate；报告有效样本/错误和上述质量指标。10/50/100 三档均有实测 p50/p95 与可得的分段耗时，能解释扫描成本和外部服务成本；合成目录不充当语义质量证明。真实服务缺失时相关比较及 live latency 明确 NOT VERIFIED，不填零或声称完成 live 比较。
- **Required tests**: 先跑 runner/metrics/split 检查；用可控计时样例验证分段边界、分位数/样本统计，再运行三档 deterministic 规模测试；显式 opt-in live 校准、两策略 benchmark 和可用的 live 规模测量。记录命令、数据/配置版本、实际样本数与脱敏结果；缺失服务列为未运行。
- **Completion evidence**: 校准与逐 case 报告、两策略对照、三档规模的 p50/p95/分段耗时、复现配置、exact scan 决策分析；live 缺失项明确 NOT VERIFIED。任务测试 → evaluation/索引回归 → diff review → focused commit → push `origin/agenthub-dev`，不编造数字。

### T44 — AgentHub 综合回归

- **Task ID**: T44
- **Priority**: P0
- **Goal**: 补齐跨层连接处的综合回归，确认模块组合后仍符合冻结语义。
- **Why this task exists**: 原子版本/索引、路由、执行终态与指标可能在接口交汇处失配。
- **Dependencies**: T13、T17、T22、T23、T24、T25、T26、T27、T28、T29、T30、T33、T42。
- **Likely files/modules involved**: `tests/` AgentHub 单元/API/持久化/集成回归与 fixtures；已完成业务模块仅在复现并定位本范围缺陷后最小修正。
- **Implementation requirements**: 设计 §22；使用 fake 模型、mock provider、真实临时 DB/resolver，覆盖版本/索引失败保留旧版、拒绝无执行、启动失败、provider failure+completed、missing outcome、取消、持久查询与 metrics 一致性。各任务单测应已存在，此项补连接面而非替代前置测试。
- **Do-not-touch boundaries**: 不把所有测试拖到此项才写，不做 live endpoint 默认依赖，不修复无关上游 fixture/源码或扩展功能。
- **Acceptance criteria**: 跨层成功/拒绝/失败/取消记录可解释且查询/metrics 一致；重开 DB 不丢业务事实；任何发现的本范围回归有复现与最小修正证据。
- **Required tests**: AgentHub 聚合测试集、关键 failure injection/事务中断、provider outcome 集成、状态竞态、API+持久化+metrics 组合与 benchmark runner 无执行回归；运行有界。
- **Completion evidence**: 测试范围、命令/退出码/结果和继承阻塞区分；运行任务测试 → 相关跨层回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T45 — Legacy ChatDev backward-compatibility regression

- **Task ID**: T45
- **Priority**: P0
- **Goal**: 证明不使用 AgentHub 的原 ChatDev Web/Launch 路径保持兼容。
- **Why this task exists**: 新增可选 hook 和前端模式不得改变原执行协议、消息与附件行为。
- **Dependencies**: T30、T36、T44、T49。
- **Likely files/modules involved**: 已有 `tests/`、`server/routes/execute.py`、WorkflowRunService、WS/session/upload/artifact/download 和 Launch；新增/扩展窄兼容测试与验证记录。
- **Implementation requirements**: 设计 §22、§24；验证原 `/api/workflow/execute`、默认 WorkflowRunService、无 recorder Message、WS/human input/upload/artifact/download、手选 YAML Launch；包含 T49 管理入口加入后的原 Launch/Workflow 页面回归，记录实际测量范围。继承 WebSocket fixture blocker 单列，不因排除文件就称完整 suite 全绿。
- **Do-not-touch boundaries**: 不为测试替身问题顺手修改生产 WebSocket 代码，不擅自修复继承 fixture 或升级依赖，不改变旧 endpoint/YAML 请求语义。
- **Acceptance criteria**: 受影响旧接口和交互有通过证据；继承阻塞与新回归分开；未测项目明确 NOT VERIFIED，默认调用无需 AgentHub recorder 或额外配置。
- **Required tests**: 有界 legacy API/service/WS 协议测试，human input、上传/产物/下载、手动 Launch 交互/构建；相关继承子集与完整集状态分别报告，不用一次 smoke 冒充全覆盖。
- **Completion evidence**: 新旧执行对照、命令/结果、UI/协议证据及 fixture blocker；运行任务测试 → 相关 legacy 回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T46 — Standard Docker / local reproducibility — HIGH RISK

- **Task ID**: T46
- **Priority**: P0 — HIGH RISK
- **Goal**: 完成标准镜像和 Compose 的本地可复现交付，验证业务数据库持久化。
- **Why this task exists**: 当前 host 构建/临时覆盖成功不证明标准前端镜像可用，业务记录也需跨容器重启保留。
- **Dependencies**: T32、T37、T44、T45。
- **Likely files/modules involved**: 已有 Dockerfile、`frontend/Dockerfile`（实施前核对实际路径）、`compose.yml`、环境示例、SQLite 持久目录配置、交付检查；仅定位问题后做必要修正。
- **Implementation requirements**: 设计 §6、§22、§25；按标准 frontend image/backend/Compose 构建启动，验证六类目录、SQLite 持久卷/目录、重启后 Agent/Run/Trace 查询与新旧入口。分别记录 build、容器运行、协议/UI 和真实 provider 证据；针对已知 npm ci 卡住先取证再最小修正。
- **Do-not-touch boundaries**: 不用 host node_modules bind mount 代替标准镜像验收，不升级无关依赖、不删除数据库/卷、不引入云部署或 Kubernetes；不把 health 200 当真实 provider 健康。
- **Acceptance criteria**: 标准双服务镜像构建/启动可复现；新旧入口可验证；重启后记录仍可查（不承诺续跑）；若构建或外部服务阻塞，准确标失败/NOT VERIFIED 而非交付通过。
- **Required tests**: 标准构建、Compose 配置检查、双服务启动/HTTP/WS、新旧入口 smoke、持久数据库的停止重启查询、相关部署配置回归；所有长命令受限时长且不回显秘密。
- **Rollback boundary**: 回退本项必要部署文件/配置改动，保留 SQLite 数据库、持久卷和已有产物；禁止通过删除数据“回滚”。
- **Targeted regression**: 标准前后端双服务构建与启动、旧 workflow 与 AgentHub 入口、SQLite 持久化及重启查询。
- **Minimum-change requirement**: 仅修正已定位的交付问题，不以依赖升级掩盖构建原因，不改变应用架构或用临时覆盖替代验收。
- **Completion evidence**: 精确标准命令、镜像/环境信息、启动与重启持久化证据；运行任务测试 → targeted regression → 检查 `git diff` → 排除无关改动 → focused commit，明确继承与新阻塞。

### T47 — README 与架构使用文档

- **Task ID**: T47
- **Priority**: P0
- **Goal**: 提供基于实际交付结果的启动、使用、测试和评测复现说明。
- **Why this task exists**: 读者需能复现平台能力并理解已验证限制，不能靠对话上下文或虚构指标。
- **Dependencies**: T43、T46。
- **Likely files/modules involved**: 已有 README 及必要使用/架构说明；引用 AGENTHUB_DESIGN、baseline、真实 benchmark 和部署证据，不改冻结架构。
- **Implementation requirements**: 设计 §23–§25；说明配置、启动、目录登记、自然语言任务、拒绝/失败语义、trace/metrics、测试与 benchmark 命令；给出架构图/执行边界和例子。说明可信内部环境、task 文本持久化与发送给 embedding/reranker、默认不发附件正文；明确 endpoint/LLM 未验证范围和 Docker 条件。
- **Do-not-touch boundaries**: 不通过文档悄悄改架构，不填虚构成绩，不把 mock/live 混淆，不承诺不可信多租户安全或重启续跑。
- **Acceptance criteria**: 命令、路径、链接与实际仓库/结果匹配；示例可按文档复现；每个性能/质量声明可追溯到实测或明确未运行，秘密不出现。
- **Required tests**: 文档链接/命令核对和必要复现 smoke；核对结果来源、配置示例与实际接口，复用已有效验证证据并标日期/环境，必要更新才重跑相关检查。
- **Completion evidence**: 命令/链接核对结果、实际报告来源及限制清单；运行任务检查 → 相关文档示例回归 → 检查 `git diff` → 排除无关改动 → focused commit。

### T48 — Business Demo & Resume Acceptance

- **Task ID**: T48
- **Priority**: P0
- **Goal**: 交付连贯的企业内部 Agent 平台业务 Demo、可复现脚本和实际 3–5 分钟视频，形成可核验的简历验收证据。
- **Why this task exists**: 同时呈现业务用户提交任务与管理员管理能力目录，证明通用注册、动态选择和可观察执行的价值，而非六个硬编码技术演示。
- **Dependencies**: T47。
- **Likely files/modules involved**: 建议 Demo 操作脚本/说明和视频产物或可访问引用；复用现有 fixtures、UI、README 与 benchmark 结果。
- **Implementation requirements**: 设计 §19–§21、§25 及本次业务验收要求；以企业研发/知识工作目录串联四个场景：①清晰任务，如比较 LangGraph/AutoGen 的企业 Agent 项目适用性（措辞可替换），展示 ResearchAgent 候选排名→选择→执行→业务状态→trace/metrics；②跨两个 Agent 能力的模糊任务，展示 semantic Top-K→可选 LLM rerank→选中项，解释 LLM 仅重排召回候选，未运行 rerank 则明确标注；③无能力匹配/低置信→NO_SUITABLE_AGENT→TaskRun.rejected，与 execution failed 明显区分；④通过 T49 注册新的兼容 Agent 或禁用现有 Agent，对同一受控任务前后比较候选/选择/拒绝变化，证明变化来自 Registry metadata/status，未增加 Router branch。注册使用已有或预审核的受控 runtime_ref，不演示任意 workflow 上传。尽可能展示 selected Agent、candidate scores/ranks、业务 Run status、routing trace、latency/token metrics 和 Registry state，unknown tokens 如实显示。单独展示真实 benchmark 与配置/限制，执行、mock 和 live 证据清楚区分，视频控制在 3–5 分钟。
- **Do-not-touch boundaries**: 不要求公有云、不为演示新增 P1 功能、不伪造视频产物/指标、不把 mock 演示称真实 provider 成功、不暴露凭据或私人任务。
- **Acceptance criteria**: 四个场景均能按脚本复现且视频可播放、时长 3–5 分钟；拒绝与执行失败有明确视觉/状态区别，管理员操作后路由行为改变且 Router 源码未改；体现企业目录与端到端平台故事。所有简历中的功能/性能声明可追溯到验收/benchmark，未验证 live 项明确 NOT VERIFIED，不把 mock 执行称真实 provider 成功。
- **Required tests**: 预演四场景并核对输入、候选、状态和 trace；用可控 backend 的集成检查验证注册/禁用前后路由变化与 Router 无源码差异；真实预演按实际服务可用性单独记录。检查视频时长/可播放性/链接/脱敏，复用 T49 管理交互及相关 smoke 证据，出现新变化才补回归。
- **Completion evidence**: 脚本、四场景检查、Registry 变更前后路由证据、Router 无改动证明、视频路径/链接与时长、简历声明对应的实测报告；任务检查 → Demo 回归 → diff review → focused commit → push `origin/agenthub-dev`。未录制视频或未完成必需场景不能标 COMPLETE。

## 依赖、风险及执行约束

先完成 Block A 的存储基础，再完成 Block B 的验证与 Block C 的 fake/index，才能完成 Registry 生命周期。T31/T32 在 Block B 中是 fixtures 任务，不是 T05 的前置条件；T13 不依赖完整 demo 目录。模块接口建立后，outcome 单测、benchmark 数据整理和 metrics 固定数据测试可相对独立开展，但每个 Codex 实施会话仍只处理一个任务，不自动启动并行实施。

HIGH RISK 任务固定为 T26、T27、T28、T29、T34、T36、T46。各项正文均给出 Rollback boundary、Targeted regression 和 Minimum-change requirement：

- T26/T27：回退可选 recorder 链，回归无 recorder 的 Message/上下文。
- T28/T29：回退 AgentHub adapter/hooks，回归旧 execute/WS/session。
- T34/T36：回退新增模式和业务投影，回归手动 Launch/附件/结果。
- T46：回退必要部署改动且保留数据库，回归标准双服务构建和持久化。

原批准计划由早期 57 项压缩为 49 项；本次只追加轻量 T49，形成 50 项，原 T00–T48 不重编号：

- 合并骨架与 metadata、manifest 与路径解析、embedding 接口与 fake、文本与版本索引、Top-K 与过滤、semantic contract 与 gate。
- 合并 WorkflowRunService adapter 与 correlation、metrics backend 与 API、ambiguous/no-match 数据任务以及相关 benchmark 指标。
- allowlist fixtures 随对应 workflow 交付。
- 明确补入 M1 验收、真实 embedding adapter、受控 re-embed 任务。
- Runtime outcome、provider failure、业务终态、取消和前端关键风险仍分别验收。

约 15 天是工作块时间盒，不是保证：A/B/C/D/E/F 分别约 2/2/3/4/2/2 天；T49 的半天至一天包含在 E 中，F 复用前置验证证据和 benchmark runner。M1 后复核剩余工作；时间紧先削减 dashboard 丰富度、视觉打磨和可选 UI 细节，不删路由拒绝、结构化执行 outcome、测试、向后兼容、Registry 泛化或 benchmark 证据。不得增加 PostgreSQL、Redis、Qdrant、Kafka、Kubernetes、微服务拆分、复杂 RBAC、多租户、Marketplace、动态团队、分布式 scheduler 或复杂 fallback 链来回应本次增强。

## P0 Critical Path

以下保留批准计划中的汇合路径，用于定位里程碑；其中区间是工作集合，实际执行严格按每项 Dependencies 拓扑展开，不能把图中的箭头误读为新的逐项依赖。

最早可用主链：

`T00 → T01 → T02/T03 + T04/T05 + T06/T07 → T08/T09/T10 → T11/T12/T13`

执行链：

`M1 → T18–T21 → T23 → T25–T30 + T31 → M2`

完整 MVP 汇合：

`M2 + T14–T17/T22/T24/T32 → T33–T37 + T49 → M3；T44 + T49 → T45 → T46`

管理员支线：`T22 → T49`，最晚 M3 完成；不阻塞 M1 路由正确性或 M2 Runtime 正确性。T45 依赖 T49 是为了验证新增管理入口后的旧页面兼容；前述箭头仍不替代各任务的完整 Dependencies。

评测支线：

`T32 → T38–T40；T15 → T41/T42；T16/T17 + 数据集 + runner → T43`

交付汇合：

`T43 + T46 → T47 → T48`

## Milestones

- **M1 — Registry + Semantic Routing**：T00–T13。SQLite 注册、发现、选中/拒绝可确定性复现，无 LLM 和 Runtime 执行依赖。
- **M2 — End-to-End Agent Execution**：M1 加 T14/T15、T18–T31。通过真实 WorkflowRunService 执行链和 mock provider 验证正常完成、provider 失败仍 completed、workflow 异常及协作取消。
- **M3 — Observable Business Demo**：M2 加 T16/T17、T32–T37、T49。业务用户自然语言入口、候选、执行、业务状态、trace/metrics 连通；管理员可登记、查看能力/version/status、启停 Registry 条目，展示目录状态如何影响路由。运行相应 UI/API 集成及旧页面回归；真实服务未验证项明确标注。
- **M4 — Evaluation + Delivery**：M3 加 T38–T48，完成全部 50 项 T00–T49。60-case 数据、两策略/阈值证据、10/50/100 目录规模实测、兼容回归、标准 Docker、本地文档及四场景业务视频；真实比较不可用时明确未运行，不能声称取得 live 结果。

里程碑成员按上述集合及其前置任务核对。每个 milestone 必须有任务专项测试、跨层集成和相关 legacy 回归证据；只有全部必需验收通过才可应用 [AGENTS.md](AGENTS.md) 的自动合并门禁。M1 用 T13 切片；M2 用 T28–T30 的真实执行链/mock provider 集成；M3 用 T34–T37/T49 的用户与管理员交互集成；M4 用 T43–T48 的评测/回归/部署/交付验收。单任务完成不得直接合并 main；本次文档调整不表示任何里程碑完成。

## Deferred P1/P2 Work

- **P1 — 明确延后**：richer Dashboard、完整 Run history UI、超出 T49 的完整 Agent 管理控制台/高级编辑与运营界面、ANN、实证驱动的本地 embedding 优化、有限 fallback、动态团队、公有云部署。T49 是本次明确授权的最小 UI 范围补充，不改变冻结技术架构；设计中原延后的完整管理体验仍留在 P1。公共或不可信多用户开放前须另行 authentication/authorization review。
- **P2 — 明确延后且不属于当前 MVP**：复杂 RBAC、多租户、Marketplace、分布式 Runtime/Scheduler、Kubernetes、自动扩缩、复杂 fallback、自主动态 DAG。
- 新 Multi-Agent Framework、复杂 A2A、复杂对话记忆、MCP Tool Router 和替换 ChatDev Runtime 继续属于非目标，不因列出后续阶段而获得实施授权。

这些工作不计入 50 项，也不作为任何 P0 任务或里程碑的验收依赖。

## Recommended First Implementation Task

**Recommended First Implementation Task = T00 — Implementation baseline guard**。

在 TASKS.md 评审后，单独授权的实施会话先确认准确基线、可用验证环境和继承阻塞项。本次文档调整在验证、commit/push 后停止，不执行 T00 或任何后续任务。本文所有任务状态均为 NOT STARTED；要求的 tests/evidence 是未来任务完成条件，不是本次已执行声明。

## 文档交付自检要求

创建或调整此清单时须完整检查：50 个 P0 任务定义 T00–T49 各一次（原 T00–T48 不重编号）、每项 11 个必填字段、有效且无环依赖、功能验收与测试、七项 HIGH RISK 的回退/回归/最小改动 guidance、M1–M4、P0 Critical Path、Deferred P1/P2、首项 T00 与未开始实施的边界。另核对 T32 企业研发/知识工作目录、T43 规模性能证据、T48 四场景业务视频、T49 完整验收/测试、M3 管理员体验、约 15 天时间盒、逐任务 push 与 milestone merge 门禁。任务引用可重复，唯一性检查针对任务定义而非全文提及次数；冻结架构与生产源码不得因此变更。

本次运行 `git status` 和 `git diff -- AGENTS.md TASKS.md` 并完整审阅；验证 `git branch --show-current` 为 agenthub-dev、`git remote -v` 中 origin 为 `git@github.com:hongmuyu/AgentHub.git`。origin 缺失才添加；指向其他地址时不得静默覆盖。仅 stage 授权的 AGENTS.md/TASKS.md，focused commit 建议 `docs: refine AgentHub P0 tasks and git workflow`，然后 `git push -u origin agenthub-dev`。保留无关工作区内容，不 force push，不合并 main；不执行 T00 或功能任务。
