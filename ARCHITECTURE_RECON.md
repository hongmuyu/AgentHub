# AgentHub Phase 0 — ChatDev 架构调查

调查日期：2026-09-27。范围：实际源码调查、启动验证和最小侵入建议；不包含 AgentHub 功能实现或技术设计冻结。

## 0. 调查基线与主要结论

已完整读取当前项目的 `AGENTS.md`。调查开始时，`/home/abc/文档/ChatGPT/AgentHub` 是尚无提交的 Git 仓库，只有项目规则和编辑器临时文件，没有 ChatDev 源码。经用户确认，从官方仓库下载源码到项目之外的临时调查目录；因此本文描述的是指定上游快照，不是已经导入 AgentHub 的实现。

- 上游：[OpenBMB/ChatDev](https://github.com/OpenBMB/ChatDev)。默认分支 `main`，README 标识为 **ChatDev 2.0 — DevAll**。
- 固定提交：[`4fb2db0ea90375ce1059f44fe03ffbd191a7a169`](https://github.com/OpenBMB/ChatDev/commit/4fb2db0ea90375ce1059f44fe03ffbd191a7a169)。提交时间：2026-07-24 16:01:27 +08:00。
- 最终源码目录：`/home/abc/文档/ChatGPT/.agenthub-recon-4fb2db0`。本文所有源码相对路径均以该目录为根；文末链接固定到上述提交，不依赖临时目录长期存在。
- 最初的 `/tmp/agenthub-recon-XDGmJ2/ChatDev` 在执行环境中断后被清理；随后重新获取并核对了相同提交。中断前没有收回结果的后端测试不计为通过。
- 上游源码没有修改；AgentHub 本次只新增本报告。没有实现 Registry、Router、数据库 schema、向量数据库或 Dashboard。

核心结论：

1. 当前核心抽象是 **YAML 配置的 workflow node**。业务 Agent 是 `Node(type="agent") + AgentConfig`，不是独立的、可注册启停的长期服务。
2. `node_registry`、`ProviderRegistry`、`schema_registry` 都已经存在，但分别注册执行类型、模型适配器和配置 schema，不能等同于平台级 Agent Registry。
3. 执行图中的节点、边、起点、终点在 YAML 中确定；现有调度依据拓扑、触发条件和循环规则，没有找到基于业务能力的 Agent 候选召回、语义排序、阈值拒绝及路由评测模块。
4. 最小接入边界位于 **服务器应用服务层、调用现有 WorkflowRunService 之前**。未来路由得到受控 `runtime_ref` 后，仍执行现有 Workflow/Runtime。
5. Web 会话状态是进程内存；YAML、执行归档、记忆、画布布局分别有自己的存储方式。它们不是统一的持久化 Run 数据层。
6. **执行完成不保证任务成功**：模拟 provider 失败后，SDK 仍返回错误文本，归档中的 `WORKFLOW_END.success` 仍为 `true`。不能直接用现有完成事件计算 AgentHub 的任务成功率。
7. 启动验证结论是**有条件可运行**：前端构建/开发服务通过；后端在跳过 `pycairo` 的诊断环境下启动、HTTP/WS 无模型工作流、重连、结果下载通过。完整标准安装因 Cairo 开发库缺失失败；完整 Docker 构建未验证完成；真实 LLM、PDF、外部工具未验证。详见第 19 节。

## 1. Agent 定义与生命周期

### 1.1 定义所在位置

- `entity/configs/node/node.py::Node`：节点 ID、type、description、context_window、vars、具体 config，以及运行中的 input/output、predecessors/successors、触发状态。
- `entity/configs/node/agent.py::AgentConfig`：provider、base_url、name、role、api_key、params、retry、input_mode、tooling、thinking、memories、skills；运行时附加 token_tracker 和 node_id。
- `runtime/node/builtin_nodes.py`：调用 `register_node_type("agent", config_cls=AgentConfig, executor_cls=AgentNodeExecutor, ...)`。
- `runtime/node/executor/agent_executor.py::AgentNodeExecutor.execute()`：Agent 的实际执行逻辑。[S01], [S02], [S03]

### 1.2 实际生命周期

1. `check/check.py::load_config()` 校验并解析 YAML；`Node.from_dict()` 根据节点 type 查找配置 schema，构造 `AgentConfig`。
2. `GraphManager._instantiate_nodes()` 深拷贝节点定义，清空拓扑链接，创建本次工作流使用的节点实例。
3. `GraphExecutor._build_memories_and_thinking()` 创建记忆/思考管理器；`NodeExecutorFactory.create_executors()` 按注册的节点类型创建 executor。一个图中相同 type 共享其类型 executor，并非每个 Agent 都有独立 executor 对象。
4. 节点被触发后，`GraphExecutor._process_result()` 按 `node.type` 调用对应 executor。
5. `AgentNodeExecutor.execute()` 查 `ProviderRegistry`，绑定 token tracker/node ID，准备工具和 skills，创建 provider/client，准备 system prompt 和 conversation。
6. 依次执行可选的前置 thinking、记忆检索、模型调用、工具循环、附件保存、后置 thinking、记忆更新，返回 `Message` 列表。
7. 图收集输出、保存记忆和执行归档。Web 路径的 `WorkflowRunService` 在 finally 中清空 session.executor/session.graph，并清理人工输入等待对象。[S03], [S04], [S05], [S06]

这里的生命周期是“加载配置 → 本次运行实例化 → 节点调用 → 归档”，没有平台 Agent 的 Register/Update/Enable/Disable 生命周期。provider/client 在节点 execute 内创建，不应把它们的对象身份作为业务 Agent ID。

## 2. Agent metadata、role、capability 结构

必须区分以下概念：

- `Node.id` 是图内节点标识；`Node.description` 是节点说明。
- `AgentConfig.name` 是 **模型名**，其 FIELD_SPECS 显示为 “Model Name”；`role` 是 **system prompt**。直接把 name 映射为平台 Agent 名称会混淆模型与业务身份。
- `runtime/node/registry.py::NodeCapabilities` 只有 `default_role_field`、`exposes_tools`、`resource_key`、`resource_limit`，用于角色字段、工具暴露和执行资源约束，不表达“调研/数据分析”等业务能力。
- `GraphManager._build_metadata()` 建立当前图的 `catalog`，包含节点 type、description、model_name、role、tools、memories、params。这是一次构图得到的快照，不提供 Agent 搜索、版本、启停或持久化目录。
- `runtime/node/agent/skills/manager.py::SkillMetadata` 有 name、description、allowed_tools、compatibility 等；`AgentSkillManager.discover()` 扫描项目 `.agents/skills`。这是选定 Agent 内部的技能发现，不是跨 Agent 路由。
- `entity/configs/graph.py::DesignConfig.version` 是工作流文档版本；`GraphDefinition.id/description/organization` 是图配置，不能直接当作 Agent 版本、业务能力或租户模型。[S01], [S02], [S05], [S07], [S08]

已查看的这些结构没有完整的业务 `AgentMetadata(id, name, capabilities, tags, version, status, runtime_ref)` 合约。未来可复用节点描述和工具声明作为人工整理 metadata 的输入，但不宜自动把大段 role prompt 当作可靠能力标签。

## 3. Runtime 入口与执行路径

### 3.1 CLI / SDK

- `run.py::main()`：解析 `--path` 等参数，`load_config()`，从终端读取 task prompt，构造 `GraphConfig` / `GraphContext`，调用 `GraphExecutor.execute_graph()`。默认路径 `yaml_instance/net_loop_test_included.yaml` 在该快照文件清单中不存在，运行 CLI 应显式传已有 YAML。
- `runtime/sdk.py::run_workflow()`：接受 yaml_file、task_prompt、attachments、session_name、variables、log_level；加载配置、构造输入和图、执行，返回 `WorkflowRunResult(final_message, meta_info)`。
- `WorkflowMetaInfo` 包含 session_name、yaml_file、log_id、outputs、token_usage、output_dir。SDK 不依赖已连接的 WebSocket，也不创建 `WorkflowSessionStore` 里的 Web 会话。[S09]

### 3.2 Runtime 主链

```text
load_config(YAML)
  → DesignConfig / GraphDefinition / Node / AgentConfig
  → GraphConfig.from_definition()
  → GraphContext
  → GraphExecutor.__init__()
      → RuntimeBuilder.build()
  → GraphExecutor._execute() → run()
      → GraphManager.build_graph()
      → edge conditions/processors + memory/thinking + node executors
      → 注入任务到 graph.start 节点
      → DAG / Cycle / MajorityVote 执行策略
      → _execute_node() → _process_result()
      → NodeExecutor.execute()
      → AgentNodeExecutor → Provider / ToolManager（若为 agent 节点）
      → 收集输出、保存 memory、ResultArchiver.export()
  → GraphContext.record()
```

`RuntimeBuilder.build()` 组装 ToolManager、function managers、LogManager、TokenTracker、AttachmentStore、code_workspace、global_state；`ExecutionContext` 把这些服务传给节点 executor。

`workflow/runtime/execution_strategy.py` 中的 `DagExecutionStrategy`、`CycleExecutionStrategy`、`MajorityVoteStrategy` 分离执行策略。`DAGExecutor.execute()` 按层运行，`ParallelExecutor.execute_nodes_parallel()` 使用线程池，`ResourceManager.guard_node()` 应用节点类型资源约束。它们已经承担运行时编排，不应为 AgentHub 重写。[S04], [S10]

## 4. Workflow 定义与 Agent 选择/绑定

`entity/configs/graph.py::DesignConfig` 包含 version、vars、graph；`GraphDefinition` 包含 nodes、edges、memory、start_nodes、end_nodes 等；外部 YAML 键是 `graph.start` / `graph.end`。

绑定是两层显式配置：

1. YAML 节点 `type: agent` → `Node.from_dict()` 查 node schema → `AgentConfig` → runtime 的 `AgentNodeExecutor`。
2. 节点 `config.provider` → `ProviderRegistry.get_provider()`；`config.name` 指定模型；`config.role` 指定 prompt。

没有从任务文本自动查找 Agent 并替换节点的步骤。`GraphManager._determine_start_nodes()` 要求显式配置 start；边定义控制执行顺序、条件、数据传递、上下文保留/清理。`GraphExecutor._get_final_node()` 优先取 end 中有输出的节点，再回退到第一个 sink node；多个终点并不会自动合并成一个最终回答。[S01], [S04], [S05]

已有复杂能力包括子图加载、循环、majority voting 和 `workflow/executor/dynamic_edge_executor.py::DynamicEdgeExecutor`。这些是既定图内的执行能力，不等于 Agent Registry 上的语义发现，也不应为 P0 自动组建团队而启用更多复杂机制。

### 4.1 六类 Demo Agent 的复用素材

以下只是已查看的配置素材，不是六个已完成的平台 Agent：

- Research：`yaml_instance/general_problem_solving_team.yaml` 的 `Information Searcher`，prompt 明确要求事实检索，工具包含 web/weather。可参考其检索职责，需移除对上游部门指令的依赖。
- Code：同文件的 `Technician`，职责包含代码、脚本、命令；`yaml_instance/ChatDev_v1.yaml` 的 `Programmer Code Review` 可参考，但依赖 `COMMON_PROMPT` 和软件团队上下文。
- Data：`yaml_instance/data_visualization_basic.yaml` 的 `Data Analyst` 实际是选择 CLEAN/VISUALIZE 的中心控制节点，不能仅凭名字当作完整通用 DataAgent。
- Document：`general_problem_solving_team.yaml` 的 `Summary Department` 整合上游部门输出；`deep_research_v1.yaml` 的 `Report Writer` 写特定章节。它们可提供总结/写作素材，但未核实到现成的通用文档比较、信息抽取 Agent。
- Planning：`general_problem_solving_team.yaml` 的 `Demand Analyze` 把需求分配给固定部门；`deep_research_v1.yaml` 的 `Planner` 面向逐章节研究。需整理成独立职责，不能把固定部门名搬进新 Router。
- Review：`deep_research_v1.yaml` 的 `Quality Reviewer` 针对逐章报告质量，属于特定领域 reviewer，不等同于覆盖所有任务的质量评估器。[S11]

整套示例通常包含多个 Agent、工具或循环。P0 的“路由到一个 Agent”需要明确运行单元，不能把路由到多 Agent 团队工作流偷偷当成已经满足该边界。

## 5. FastAPI / server 入口与相关 API

入口是 `server_main.py::main()`：配置日志，运行 `uvicorn.run("server.app:app", ..., ws="wsproto")`；参数默认端口 8000，Makefile/README 示例使用 6400。`server/app.py` 创建 FastAPI，`server/bootstrap.py::init_app()` 安装 CORS、异常处理、中间件、全局状态和路由。`server/routes/__init__.py::ALL_ROUTERS` 汇总路由；`server/state.py::get_websocket_manager()` 提供进程内 singleton。[S12]

关键端点及符号：

- `POST /api/workflow/execute` — `server/routes/execute.py::execute_workflow()`；要求已连接 session，`asyncio.create_task()` 调用 `WorkflowRunService.start_workflow()`，立即返回 started。此响应只说明已派发。
- `POST /api/workflow/run` — `server/routes/execute_sync.py::run_workflow_sync()`；默认通过 `run_in_threadpool(run_workflow, ...)` 返回最终结果。Accept 包含 `text/event-stream` 时运行 `_run_workflow_with_logger()`，通过队列发送 started/log/completed/error SSE 事件。它不是 WebSocket 会话执行入口。[S13]
- `WS /ws?session_id=...` — `server/routes/websocket.py::websocket_endpoint()`；管理连接、接收消息、断开连接。`MessageHandler.handle_message()` 分发 human_input、ping、get_status、cancel。
- `GET /api/workflows` — `server/routes/workflows.py::list_workflows()`；同文件提供 `{filename}/args`、`/desc`、`/get`，以及 upload/content、update、delete、rename、copy。
- `POST /api/config/schema`、`POST /api/config/schema/validate` — `server/config_schema_router.py::get_schema()` / `validate_document()`；提供动态配置 schema 和文档校验。
- `POST/GET /api/uploads/{session_id}` — `server/routes/uploads.py::upload_attachment()` / `list_attachments()`。
- `GET /api/sessions/{session_id}/artifact-events`、`.../artifacts/{artifact_id}` — `server/routes/artifacts.py::poll_artifact_events()` / `get_artifact()`。
- `GET /api/sessions/{session_id}/download` — `server/routes/sessions.py::download_session()`；下载 `WareHouse/session_{session_id}` 的 ZIP。
- `POST /api/workflows/batch` — `server/routes/batch.py::execute_batch()`；CSV/Excel 批任务执行。
- `POST /api/vuegraphs/upload/content`、`GET /api/vuegraphs/{filename}` — 画布内容保存/读取。
- `GET/POST /api/tools/local` — `server/routes/tools.py::list_local_tools()` / `create_local_tool()`；本地工具目录能力，不是 Agent Registry。
- `GET /health`、`/health/live`、`/health/ready` — `server/routes/health.py`。它们返回固定状态，不检查 LLM、外部工具或完整依赖健康。

没有找到 Agent CRUD/启停/能力查询 API，也没有平台 Run 列表、路由评测或汇总 metrics API。`get_status` 是现有 WebSocket 消息，不能凭 `WorkflowSessionStore.list_sessions()` 的存在推断已有对应 REST 列表接口。

## 6. Task / Run / Session / State 模型

- `server/models.py::WorkflowRequest`：yaml_file、task_prompt、session_id、attachments、log_level，用于异步 Web 执行。
- `WorkflowRunRequest`：yaml_file、task_prompt、attachments、session_name、variables、log_level，用于同步/SSE 执行。
- `server/services/session_store.py::WorkflowSession`：session_id、yaml_file、task_prompt、附件、status、created_at/updated_at、graph/executor、current_node_id、人工输入 Future、results、error_message、artifact_queue、cancel_event、message_buffer。
- `SessionStatus`：idle、running、waiting_for_input、completed、error、cancelled。它没有名为 pending/success/failed 的状态。
- `WorkflowSessionStore`：create/get/has/update/complete/error/pop，以及 info/list/snapshot；由 WebSocketManager 持有。
- `workflow/graph_context.py::GraphContext`：图节点、边、层、起点、子图、循环信息、outputs、输出目录；属于可变执行上下文。
- `runtime/node/executor/base.py::ExecutionContext` / `workflow/runtime/runtime_context.py::RuntimeContext`：运行时服务和共享状态的容器，不是可持久化 TaskRun。
- `server/services/batch_parser.py::BatchTask`：row_index、task_id、task_prompt、attachment_paths、vars_override。`BatchRunService.run_batch()` 统计成功/失败，`_run_single_task()` 直接构造 GraphExecutor；并非逐条走 Web WorkflowSession 生命周期。
- `entity/messages.py::Message`、`MessageBlock`、`AttachmentRef`、`ToolCallPayload`、`FunctionCallOutputEvent`：执行消息和多模态/工具数据结构，不是 Run 状态模型。[S14], [S15]

同一个 session_id 再次调用 `create_session()` 会覆盖字典中的旧 session；目前也没有独立的 task_id → 多次 run_id 的持久关系。人工输入分支直接修改部分 session 字段，不能假设所有状态变化都经过 `update_session_status()` 或都刷新 updated_at。

## 7. 状态持久化机制

### 7.1 Web 会话与恢复

`WorkflowSessionStore._sessions` 是内存字典。WebSocketManager 断开时保留 session；重连时回放最多 1000 条 `message_buffer`，再发 `session_resumed` 快照。`_gc_loop()` 每小时检查一次，对超过 24 小时的终态会话清理。`ArtifactEventQueue` 也是进程内队列，默认上限 2000 条。[S14], [S16]

这支持同进程内断线重连，**不支持服务重启后恢复会话或续跑**。未知 session_id 重新连接时不会从磁盘恢复旧执行状态。多 worker 也不会自动共享这些字典或 Future。

### 7.2 文件与已有数据库用途

- Workflow：`server/settings.py::YAML_DIR = Path("yaml_instance")`；`workflow_storage.py::persist_workflow()` 将 YAML 文本直接写入文件。`validate_workflow_content()` 调用现有配置/逻辑检查。
- 图运行输出：`GraphContext.record()` 保存 `node_outputs.yaml` 和 `workflow_summary.yaml`；输出根为 `WareHouse`，Web session 使用 `session_{session_id}` 目录。
- 执行日志/token：`ResultArchiver.export()` 保存 `execution_logs.json` 和 `token_usage_{graph.name}.json`。
- 工作空间/附件：`RuntimeBuilder.build()` 创建 `code_workspace/attachments`；`AttachmentStore` 与 AttachmentService 处理附件及产物。
- 画布：`server/services/vuegraphs_storage.py` 已经使用 **SQLite**。默认 `data/vuegraphs.db`，可由 `VUEGRAPHS_DB_PATH` 覆盖；表 `vuegraphs(filename PRIMARY KEY, content)` 保存前端画布载荷，`save_vuegraph_content()` / `fetch_vuegraph_content()` 读写。这不是 Run/Agent 数据库。
- 记忆：`SimpleMemory.load()/save()` 使用 JSON；`FileMemory` 保存文件索引信息；现有检索代码使用 **FAISS**。`Mem0Memory.load()/save()` 是 no-op，由外部 Mem0 服务管理存储。`uv.lock` 中还有由 mem0ai 引入的 qdrant-client 等依赖，不能据此断言项目已部署向量数据库服务。[S06], [S17], [S18]

以上均是现有机制描述；本报告没有为 AgentHub 选择 SQLite、PostgreSQL、FAISS、Qdrant 或新增存储服务。

归档的局限：`GraphExecutor.run()` 在正常结束路径调用 `ResultArchiver.export()`，`_execute()` 在 run 返回后才调用 `GraphContext.record()`。若中途异常向上传播，这些完整归档不保证生成；零散服务器日志不等于一条可靠终态 Run 记录。

## 8. 前端任务提交流程

Vue 3 / Vite / Vue Router；`frontend/src/router/index.js` 将 `/launch` 绑定 `LaunchView.vue`，工作流编辑入口是 `/workflows/:name?`。请求 helper 位于 `frontend/src/utils/apiFunctions.js`。[S19]

`frontend/src/pages/LaunchView.vue` 的实际链路：

1. `loadWorkflows()` / `fetchWorkflowsWithDesc()` 获取 workflow 文件与描述。
2. `handleYAMLSelection()` 经 `fetchWorkflowYAML()` 拉取 YAML，`js-yaml` 解析；读取 `graph.initial_instruction`，准备节点和画布。
3. `establishWebSocketConnection()` 连接 `/ws`。connection 消息携带服务端 session_id，设置 `isConnectionReady`。
4. 附件经 `uploadFiles()` / `apiFunctions.js::postFile()` 上传到 session；后续提交附件 ID。
5. `launchWorkflow()` 要求选择 YAML、有 prompt 或附件、WS 已就绪；POST `/api/workflow/execute`，发送 yaml_file、task_prompt、session_id、attachments。
6. 收到 started 后把 UI 置为 Running，并将 workflow/session 写入 URL query 以支持刷新重连。[S20]

`frontend/vite.config.js` 开发代理把 `/api` 和 `/ws` 转给 `VITE_API_BASE_URL`（默认 localhost:6400）。HTTP helper 主要使用同源路径；生产部署不能只设置 WebSocket 的 API base 而忽略 `/api` 同源代理需求。

编辑器与执行文件不是同一存储：`WorkflowView.vue` 调用 `updateYaml()` 保存可执行 YAML，调用 `postVuegraphs()` 保存画布载荷；后者进入 SQLite。画布位置变化不等于工作流运行配置发生变化。

当前 Launch 页强依赖先选择 workflow。AgentHub 的自然语言任务入口需要一个小的新增交互/入口，不能假设去掉一个下拉框就完成了后端自动路由。

## 9. 前端执行/结果数据流

Web 后端链：`WorkflowRunService.start_workflow()` 创建并更新 session，发送 workflow_started；`_execute_workflow_async()` 创建 `WebSocketGraphExecutor`，其 `execute_graph_async()` 将同步 Runtime 放入线程池执行。`WebSocketLogger.add_log()` 复用 WorkflowLogger 后经 `send_message_sync()` 把日志发送回拥有 socket 的事件循环。[S06], [S16], [S21]

前端 `LaunchView.vue::processMessage()` 消费：

- `log/NODE_START`：活跃节点高亮。
- `log/MODEL_CALL`、`log/TOOL_CALL` 的 before/after：加载状态和 UI 时长。
- `log/NODE_END`：节点输出进入聊天展示，结束对应加载状态。
- `artifact_created`：附件卡片；图片再通过 `getAttachment()` 请求 artifact API 获取内容。
- `human_input_required`：显示人工输入；`sendHumanInput()` 发送 human_input 消息，后台 `SessionExecutionController` 完成等待 Future。
- `workflow_completed`：显示 summary 并设置 Completed；服务端还携带 results/token_usage，但当前 handler 主要使用 summary，不能据此声称已有完整 metrics 展板。
- `error` / `workflow_cancelled`：设置相应终态；`cancelWorkflow()` 经 WS 发 cancel。
- `session_resumed`：在消息回放后恢复 YAML、状态和当前节点；下载日志使用 `fetchLogsZip()`。[S20], [S21]

注意 `GraphContext.final_message()` 返回“多少个节点输出”的完成摘要；真正最终业务回答由 `GraphExecutor.get_final_output_message()` 提取。WebSocket 完成 summary 与同步 API 的 final_message 语义不同。

产物路径为 `WorkspaceArtifactHook → ArtifactDispatcher.emit_workspace_artifacts()/emit() → ArtifactEventQueue + WS artifact_created`；这里的 dispatcher 分发产物事件，不选择 Agent。

## 10. 现有 registry / router / dispatcher / schema / trace / metrics

### 10.1 Registry 与 schema

- `utils/registry.py::Registry` / `RegistryEntry`：惰性加载组件注册表，提供 register/get/names/items/metadata_for；重复名字报错。没有业务 Agent 更新、状态筛选和持久化生命周期。
- `runtime/node/registry.py::NodeRegistration` / `register_node_type()`：节点类型、config class、executor class、NodeCapabilities，并同步注册 node schema。
- `runtime/node/agent/providers/base.py::ProviderRegistry`：openai/gemini 等 provider 适配器注册。
- `runtime/node/agent/memory/registry.py`、`thinking/registry.py`、`runtime/edge/conditions/registry.py`、`runtime/edge/processors/registry.py`：记忆、思考、边条件、边处理插件扩展。
- `schema_registry/registry.py`：NodeSchemaSpec、EdgeConditionSchemaSpec、EdgeProcessorSchemaSpec、MemoryStoreSchemaSpec、ThinkingSchemaSpec、ModelProviderSchemaSpec 及其注册/查询函数。
- `runtime/bootstrap/schema.py::ensure_schema_registry_populated()`：导入内建注册模块；`utils/schema_exporter.py::build_schema_response()` 向前端导出配置描述。[S07], [S22]

### 10.2 路由与调度

- FastAPI APIRouter：HTTP URL 分发。
- `MessageHandler.handle_message()`：WebSocket 消息类型分发。
- `GraphExecutor._process_result()` / `NodeExecutorFactory`：按节点 type 分派 executor。
- DAG/Cycle/Parallel/DynamicEdge executor：已定义图的调度。
- `ArtifactDispatcher`：产物通知分发。
- `AgentSkillManager.discover()`：Agent 内 skills 目录发现。

在 runtime/workflow/server/entity/utils/tests 中检索并阅读上述相关实现后，未找到业务 Agent Registry、semantic router、Top-K Agent 候选、NO_SUITABLE_AGENT 处理或路由 benchmark。不能把名称中出现 routing/capability 的工具内部逻辑视为已经实现这些能力。

### 10.3 Trace 与 metrics

- `utils/logger.py::LogEntry`：timestamp、level、message、node_id、workflow_id、event_type、details、duration。
- `entity/enums.py::EventType`：WORKFLOW_START/END、NODE_START/END、MODEL_CALL、TOOL_CALL、EDGE_PROCESS、MEMORY_OPERATION、THINKING_PROCESS、HUMAN_INTERACTION 等。
- `WorkflowLogger` / `utils/log_manager.py::LogManager`：节点/模型/工具计时、日志汇总和保存。
- `utils/token_tracker.py::TokenUsage` / `TokenTracker.record_usage()/get_token_usage()`：总量、按 node/model 聚合、调用历史及执行次数。虽然 docstring 使用 singleton 一词，`RuntimeBuilder.build()` 实际为图创建实例，不能当全平台 singleton。
- `utils/structured_logger.py::StructuredLogger` 与 `utils/middleware.py`：请求 correlation ID、耗时、异常及结构化日志。
- `BatchRunService`：单任务 duration_ms、成功失败计数、`batch_results.csv`/`batch_manifest.json` 输出。[S23], [S24]

这些可为运行观测提供基础数据，但没有平台 Agent 维度聚合、路由候选分数、路由耗时、版本对比或 routing accuracy/recall。`WorkflowLogger.get_execution_summary()` 的 total_duration 使用毫秒，LogEntry.duration/计时器多为秒，批处理使用 duration_ms；汇总前必须统一单位。按 node 汇总全部日志 duration 还可能重复计入嵌套模型/工具耗时，不能当作纯 wall-clock 时间。

## 11. AgentHub 可直接复用的组件

“直接复用”指调用现有职责，不保证无需任何适配：

- 配置加载/检查：`load_config()`、`validate_design()`、`check_workflow_structure()`，保留 YAML、节点类型和拓扑校验。
- Agent 执行：`AgentNodeExecutor`、ModelProvider/ProviderRegistry、现有 prompt/messages、retry、thinking、memory/skills 集成。
- Tool 系统：`ToolManager.get_tool_specs()/execute_tool()`、`utils/function_manager.py::FunctionManager`、MCP remote/local 路径，不再构造 MCP Tool Router。
- Workflow Runtime：GraphExecutor、GraphManager、现有执行策略与资源约束，继续承接实际任务运行。
- Web 执行链：WorkflowRunService、WebSocketGraphExecutor、WebSocketManager、人工输入、附件上传和产物展示。
- 观测基础：LogManager/WorkflowLogger、TokenTracker、ResultArchiver；复用日志/耗时/token 原始数据。
- 前端：Launch 页节点/输出/附件展示、Workflow 编辑器及 schema 驱动配置表单。
- 基础交付：现有 pyproject/uv.lock、前端 lockfile、Dockerfile/compose.yml、测试目录。

现有 batch 能作为离线批量执行的参考，但不是路由评测框架：它按指定 YAML 跑任务，缺少 expected agent 标签与 Top-K 统计，且明确拒绝顶层 human 节点。不能直接把批执行成功率当 routing accuracy。

## 12. 可能需要扩展的组件与缺口

以下是缺口评估，不是本次实现：

1. **业务身份与能力目录**：补足稳定 Agent ID、display name、capabilities、tags、version/status、runtime_ref；与 provider/model/node type 分离。
2. **发现和路由服务**：状态/条件筛选、语义召回、候选排序、可选 rerank、置信阈值和拒绝结果。
3. **提交适配**：增加只提交自然语言任务的应用入口，路由完成后把选定 runtime_ref 转为既有 workflow 执行参数。
4. **Run 关联和终态记录**：在 session 创建之前就能记录任务/路由失败，关联业务 Agent 与 workflow/session/node；保留异常和取消信息。
5. **可靠成功语义**：分离“控制流完成”“Agent 执行失败”“业务质量是否满足”；先处理现有错误文本被当成成功的问题。
6. **持久追踪和观测聚合**：补路由 trace、跨 run 查询、Agent 使用量、耗时/成功率汇总；存储技术待设计评审。
7. **评测**：新增有人工标签的 benchmark 和实际执行统计，不能凭现有例子推导准确率。
8. **前端小范围扩展**：自然语言提交、选中 Agent/路由理由与候选、无匹配说明、Run 状态关联；保留旧 workflow Launch 行为。

不需要为这些缺口新增执行框架、分布式 scheduler、动态 Agent team、RBAC 或复杂 fallback 链。

## 13. Agent Registry 推荐插入点

**建议把业务 Registry 放在 `server/services` 同层的应用服务边界，HTTP 接口从 `server/routes` 汇入 `ALL_ROUTERS`。** 理由是现有业务提交和 WorkflowRunService 已在此层，Registry 可提供路由需要的 metadata 和运行引用，而无需改变底层节点类型注册。

边界建议：

- Registry 保存“可被用户任务选中的业务 Agent”，不保存活动 provider/client、线程、Future 或 GraphContext。
- runtime_ref 指向经校验的运行配置。注册/更新时使用既有配置检查验证引用，执行时再确认目标存在和可用。
- 可以参考 `utils.registry.Registry` 的重复检测与查询风格，但不能声称它已支持 Agent Update/Search/Enable/Disable；不应把业务条目注册成新的 node type。
- 不把 `schema_registry` 当 metadata 数据库，也不直接向现有 vuegraphs 表塞 Agent 记录。
- 存储后端、API 具体路径、索引实现和版本策略在后续技术设计中决定，本阶段不选型。

这是新增业务服务的职责位置建议，尚未创建任何对应文件或数据模型。[S06], [S07], [S12], [S22]

## 14. Agent Discovery / Routing 推荐插入点

建议路由发生在 **任务接收之后、创建/执行 ChatDev workflow 之前**：

```text
自然语言 task
  → Registry 读取可用能力 metadata
  → 状态/必要条件过滤
  → semantic matching → Top-K
  → 可选 LLM rerank
  → selected agent 或 NO_SUITABLE_AGENT
  → runtime_ref 解析与校验
  → 现有 WorkflowRunService / Runtime
```

明确约束：

- Router 依赖能力 metadata，不依赖六个 Agent 名称的 if/else。
- 无匹配、低置信度、引用失效属于运行前结果，不应伪装成某个不相关 Agent 的成功执行。
- 不在 DAGExecutor、AgentNodeExecutor 内做全局 Agent 检索；这些组件已接收到绑定好的节点。
- `SimpleMemory`/`FileMemory` 是 Agent 记忆检索，含记忆写入、角色条件、文本处理等语义；不能直接复用为 Agent 目录索引。可评估底层 embedding 适配代码，但不在本报告选 embedding 模型或向量存储。
- 候选召回与 rerank 分开，便于比较 semantic-only 与 semantic+rerank。保存策略、分数和选中结果供真实评测。

当前代码没有该路径；上图仅表示建议的前置应用服务边界。

## 15. Workflow / Runtime 推荐集成方式

优先沿 Web 主路径接入：路由服务选中 Agent 后，将受控 runtime_ref 解析为当前服务可接受的 YAML 文件名，然后调用 `WorkflowRunService.start_workflow()`，复用 session、WebSocket 日志、人工输入和附件处理。纯 Python/离线评测可使用 `runtime.sdk.run_workflow()`。[S06], [S09]

需要提前冻结的最小运行合约：

- P0 一个业务 Agent 对应一个明确的独立运行单元。最容易复用当前入口的方式是：为其绑定一个预先验证的、只包含一个业务 Agent 的薄 workflow；必要的确定性辅助节点是否允许，需在设计评审中明确。
- 该 workflow 继续使用已有 `type: agent`，复用 AgentConfig/provider/tools；不新增六种专用 executor 类。
- 不直接把复杂多 Agent 示例团队当作单 Agent，不在每次任务执行时动态生成 DAG。
- 若改为 runtime_ref 指向“某 YAML 中的一个 node”，就必须处理 start/end、外部依赖、子图、memory/vars 的切片问题，当前 API 没有这样的直接执行合约；这比绑定完整薄 workflow 更侵入，暂不建议作为首选。
- `WorkflowRunService._resolve_yaml_path()` 只允许 YAML_DIR 下安全文件名；SDK/sync API 接受的路径范围更宽。runtime_ref 应保持受控映射，不能直接接受用户任意路径。
- Web attachments 是上传后的 ID，SDK/sync attachments 是文件路径；适配时不能原样混传。
- 异步 execute API 当前把 log_level 设为 None；新增观测能力前要明确实际配置来源，不能以请求字段存在为依据认定生效。

不建议绕过 GraphExecutor 直接调用 provider；那会丢失已有日志、工具执行、附件、memory、thinking 和图归档。

## 16. Run State 与 Trace 推荐方式

### 16.1 复用状态语义，增加必要关联

保留 ChatDev 现有 SessionStatus 和 WebSocket 协议，在 AgentHub 应用层维护简单 Run 投影：pending → running → success/failed。投影需要保留 native_status 与终止原因，而不是粗暴改写原枚举：

- 提交/路由中的任务：pending；不等同于“只有 WS 连接、还没提交任务”。
- 原 running / waiting_for_input：运行中的任务，保留 waiting_for_input 子状态。
- 原 completed：只表示工作流结束；只有满足明确的执行成功条件后才能映射 success。
- 原 error：failed。
- 原 cancelled：单独保留取消原因和原始状态；是否在四态显示中归入 failed、如何计入失败率，应由设计明确，不与模型失败混为一谈。
- NO_SUITABLE_AGENT：无需创建 ChatDev executor；记录路由拒绝终态和 reason code，单独统计拒绝率。

Session 仍管理连接和人工输入；TaskRun 表达一次平台提交，RoutingTrace 表达决策，AgentRun 表达实际执行。它们是建议职责，不是本次新增 schema。

### 16.2 记录边界

- 在进入 Router 前创建唯一 run_id，关联 task、选中 agent_id/version 和 runtime_ref 快照；不要复用 model name 作为身份。
- RoutingTrace 记录过滤/候选 ID、分数、strategy、阈值、选中 Agent 或拒绝原因、路由耗时。分数不自动等于概率，需要基于 benchmark 校准。
- 对执行记录关联 session_id、workflow/design ID、实际 node_id、输出目录；节点循环或重复调用增加执行序号。现有 TokenTracker 已有每节点调用次数，但调用次数不一定等于业务 AgentRun 次数。
- 复用现有 LogEntry/EventType/TokenTracker；以 run/session 关联旁路路由 trace，无需改写所有 Runtime 日志。
- 在应用服务成功/失败/取消边界保存终态，即便图未成功归档也保留失败记录。
- 不直接把 trace 预先写入 `GraphConfig.metadata` 并假设保留：`GraphManager._build_topology_and_metadata()` 会用 `_build_metadata()` 重建该字典。需在设计中选择显式关联字段或旁路记录，不能依赖当前字典碰巧传递。
- 观测使用一致的 monotonic duration 与可读时间戳；区分路由、排队、执行、人工等待时间。token 从 provider 现有返回值汇总，缺失时记未知，不虚构为真实零消耗。

重启后查询终态记录与重启后续跑是不同能力。P0 可先保障记录可查询；不因此构造可恢复分布式状态机。[S05], [S14], [S23]

## 17. 高风险修改区域

### 17.1 已实测：模型异常被包装成正常输出

`AgentNodeExecutor.execute()` 的 except 捕获模型流程中的异常，记录 error 后返回 assistant 错误文本。外层继续收集输出；`ResultArchiver.export()` 固定调用 `record_workflow_end(success=True)`。

验证中仅 mock `OpenAIProvider.create_client()` 抛出 `RuntimeError("recon synthetic provider failure")`，未调用外部模型。观察到 SDK 正常返回、最终文本包含该错误、`graph_summary.execution_completed=True`、`WORKFLOW_END.details.success=True`。

**影响：**直接统计 completed 会高估成功率；一般性的“整个 Runtime 抛异常才失败”封装无法捕获这种情况。后续应先定义结构化失败传播合约，并对旧 workflow 行为做兼容验证；本次未修复。[S03], [S18]

### 17.2 会话、线程与取消

`send_message_sync()` 使用 `run_coroutine_threadsafe()` 交给拥有 WebSocket 的 loop；Runtime 在线程池执行。改变这一模型可能导致跨 loop 错误或阻塞。当前同 ID session 覆盖、无全局共享 store、Future 等也限制了并发和多 worker 使用。`request_cancel()` 是协作取消，不能据此保证在途同步 provider/tool 调用立即中止。[S06], [S16]

### 17.3 图语义和输出合约

修改 start/end、trigger、carry_data、keep_message/context_window、dynamic edge、cycle 或 subgraph 的行为可能破坏现有 workflow。平台路由应在图外做。多个终点、嵌套子图 token/输出关联须单独验证，不能默认等于一个 Agent 的完整结果。[S04], [S05]

### 17.4 Schema 与前端联动

`Node.from_dict()`、FIELD_SPECS、schema_registry、schema_exporter 和前端表单共用配置合约。向 AgentConfig 或 node type 强塞业务字段可能影响 YAML 验证、编辑器序列化和已有示例。业务 metadata 与运行配置先分离。[S01], [S02], [S22]

### 17.5 metadata / 归档 / 指标误读

graph.metadata 会重建；异常路径归档不保证完整；workflow summary 不是最终答案；时长单位不统一；嵌套日志的 duration 可能重复计数。新增 Trace/Metrics 时要明确数据来源，不能只扫一个文件就宣称覆盖全部执行。[S05], [S18], [S23]

### 17.6 路径与工具执行边界

YAML 路径、SDK 附件路径和 Web 附件 ID 的约束不同。`runtime/node/executor/python_executor.py::PythonNodeExecutor._run_process()` 会启动 subprocess；ToolManager 可以执行本地函数/MCP 工具。它们属于现有执行能力，不自动提供面向不可信企业用户的隔离。新增路由不能把未校验的运行引用或秘密配置暴露到能力 metadata/trace 中。此处只标识接入风险，不扩展 RBAC、沙箱或安全平台范围。

### 17.7 现有测试基线不是全绿

73 项测试收集成功；排除 WebSocket 文件后 66 项通过。完整测试在 `test_send_from_main_thread` 内阻塞，35 秒 timeout 退出 124；该文件独立运行也在 25 秒 timeout 退出 124。

faulthandler 栈指向 `WebSocketManager.connect() → _send_raw() → _encode_ws_message() → _json_default() → unittest.mock`。源码 `_make_manager()` 传入未约束的 MagicMock session_store；has_session 的 truthiness 导致进入重连分支，snapshot 也是 MagicMock，默认 JSON 转换调用其 to_dict 又生成 mock。该证据指向测试替身不符合真实 snapshot 合约，不代表所有真实 WebSocket 会话都失败。真实 HTTP/WS/重连烟雾测试本次通过。仍应在后续开发前恢复可用的完整回归基线，本次未修改测试。[S16], [S25]

## 18. 最小侵入修改提案（仅供评审）

建议保留现有执行链，只在任务进入它之前补平台决策，在它之外补 Run 记录和观测关联。

```text
新增自然语言任务入口（旧 Workflow Launch 保留）
  → 新增应用层 Registry + Discovery/Routing
  → 选中一个业务 Agent / 记录拒绝
  → 受控 runtime_ref → 现有 YAML 工作流
  → WorkflowRunService → WebSocketGraphExecutor → GraphExecutor
  → 原 Provider / Tool / Memory / Workflow 能力
  → 原日志、token、附件、输出 + 应用层 Run/路由记录
```

未来可能涉及的现有位置与最小目的：

- `server/routes/__init__.py`、`server/models.py`：接入新的业务提交/目录接口合约，旧请求保持兼容。
- `server/services` 同层：增加 Registry、Discovery/Router 与执行适配职责；具体文件名和存储实现待设计冻结，不在本次创建。
- `server/services/workflow_run_service.py` / `session_store.py`：必要时增加运行关联与终态通知接口；保留现有 session/人工输入/取消路径。
- `yaml_instance`：后续整理经过审核的单 Agent 运行配置，优先复用现有 prompt/tool 素材，不机械复制团队 workflow。
- `frontend/src/pages/LaunchView.vue` 或独立的小入口：支持自动路由任务提交，并复用现有执行/结果展示。页面放置方式待评审。
- `runtime/node/executor/agent_executor.py` 的错误传播边界：仅在成功/失败合约确定后评估最小兼容修正，不进行顺手重构。
- 测试与文档：逐功能增加目录、路由、API、runtime 接入、状态、拒绝/失败和真实 benchmark 验证。

建议后续顺序及各自验收点：

1. 评审本报告并冻结业务 Agent 与 runtime_ref 的对应关系、失败/取消定义、持久化边界；确认启动/回归基线。
2. Registry：注册、更新、查询、启停及引用有效性独立可测。
3. Discovery/semantic routing：验证状态过滤、Top-K、低置信拒绝；不调用 runtime 即可评测路由。
4. 执行接入：选中 Agent 通过现有 Workflow 跑通；验证原 Launch 不受影响、附件与日志不丢失。
5. Run/trace/metrics：验证拒绝、provider 失败、图异常、取消均有可解释终态；指标单位一致。
6. Evaluation/交付：以真实标注数据比较策略，完善 Docker、README 和 Demo；不编造基准数值。

本提案没有选择数据库、向量索引、embedding/rerank 模型或公共部署方式，也没有引入动态团队、分布式调度、自动 Agent 切换或新重试链。**本次工作到架构报告为止；以上步骤不会自动执行。**

## 19. 启动与验证记录

### 19.1 环境与依赖事实

实测工具：系统 Python 3.13.5、Node v24.10.0、npm 11.6.1、uv 0.10.9、Docker 29.2.1；Docker daemon 可响应。uv 下载并使用 CPython 3.12.13。

`pyproject.toml` 要求 `>=3.12,<3.13`，因此 README 的“Python 3.12+”不能作为接受 3.13 的依据。前端安装后的 Vite 为 7.3.0，构建使用 Node 24；本次未验证 README 所列最低 Node 版本。[S26]

### 19.2 标准安装：失败，原因已定位

在未修改依赖文件的官方 checkout 中执行：

```bash
uv sync --frozen --python 3.12
pkg-config --modversion cairo
```

第一条退出 1；`pycairo==1.29.0` 编译失败，Meson 报 `Dependency "cairo" not found`。第二条也退出 1，确认缺少 Cairo pkg-config 描述。依赖链为 `xhtml2pdf → svglib → rlpycairo → pycairo`。没有修改系统软件包或依赖声明来掩盖此失败。

官方 Dockerfile builder 已安装 `pkg-config`、`build-essential`、`python3-dev`、`libcairo2-dev`，runtime 安装 libcairo2；这是源码中现有的环境处理，不是本次新增。

### 19.3 前端：构建与开发服务通过

首次同提交 checkout 的 frontend 目录执行：

```bash
npm ci --no-audit --no-fund
npm run build
VITE_API_BASE_URL=http://127.0.0.1:16400 npm run dev -- --host 127.0.0.1 --port 15173 --strictPort
```

- npm ci 成功安装 216 个包，退出 0。
- Vite build 退出 0，161 modules transformed，生成 dist。
- 开发服务报告 ready，访问 `http://127.0.0.1:15173/` 得到 HTTP 200。
- 未进行人工浏览器点击验收；HTTP 首页可达与完整 UI 交互验收是不同证据。

### 19.4 后端：诊断环境下启动与协议执行通过

为隔离 Cairo 编译阻塞，仅在调查 checkout 的虚拟环境执行：

```bash
uv sync --frozen --python 3.12 --no-install-package pycairo
.venv/bin/python server_main.py --host 127.0.0.1 --port 16401
```

第一条安装成功，**但该环境有意缺少 pycairo，不是完整依赖验证通过**。后续使用 `.venv/bin/python`，避免 `uv run` 再次同步回完整依赖并触发相同失败。没有配置真实模型凭据，也没有调用外部模型。

最终受控探针在同一进程内启动后端、轮询 `/health` 等待就绪、执行以下验证并停止该后端，脚本退出 0：

- `/health`、`/health/live`、`/health/ready`、`/api/workflows`：HTTP 200。
- `POST /api/config/schema`，请求 `{}`：HTTP 200。
- `POST /api/workflow/run`，指定现有 `demo_loop_counter.yaml`：`status=completed`，`final_message=Final summary released`。
- `/ws` 建立连接后，POST `/api/workflow/execute` 执行同一无模型示例：收到 35 条业务消息，包含 WORKFLOW_START、NODE_START、NODE_END、WORKFLOW_END 和 workflow_completed；结果包含 Finalizer 节点输出。
- 同 session_id 重连：回放 35 条缓冲消息，`session_resumed.status=completed`。
- `/api/sessions/{session_id}/download`：HTTP 200，返回有效 ZIP 头。
- 模拟 provider 失败：验证了第 17.1 节的错误文本/成功标志不一致。

`demo_loop_counter.yaml` 使用 literal 与 loop_counter 节点，不涉及 LLM。这证明 HTTP/WS、图执行、归档、重连等基础链路可运行，**不证明 Agent 模型调用、真实工具、MCP、Mem0 或 PDF 导出成功**。`functions/function_calling/deep_research.py::report_export_pdf()` 在函数内导入 xhtml2pdf；当前诊断环境不用于宣称其可用。

### 19.5 测试：部分通过，完整集阻塞

```bash
timeout 35 .venv/bin/python -m pytest -q -o faulthandler_timeout=15
timeout 25 .venv/bin/python -m pytest -q tests/test_websocket_send_message_sync.py
timeout 25 .venv/bin/python -m pytest -q --ignore=tests/test_websocket_send_message_sync.py
```

- 完整集收集 73 项，第一条超时退出 124，阻塞位置见第 17.7 节。
- WebSocket 文件单独收集 7 项，第二条超时退出 124。
- 第三条退出 0：**66 passed, 1 warning in 2.12s**。warning 是 RequestsDependencyWarning，涉及锁定环境中的 urllib3/chardet/charset_normalizer 版本兼容提示。
- 没有把排除测试后的结果当作完整 suite 通过，也没有修改任何测试。

### 19.6 Docker / Compose：尚未完成验证

```bash
docker info --format '{{.ServerVersion}}'
docker compose config --quiet
docker build --target runtime -t agenthub-recon:4fb2db0 .
```

- daemon 检查成功，版本 29.2.1。
- Compose config 退出 1：checkout 缺少 `.env`。`compose.yml` 同时要求 `.env` 和 `.env.docker`，未创建这些文件或复制其他项目凭据。
- Docker build 已开始官方镜像及系统依赖下载，但在执行环境中断前没有获得构建成功/失败终态；因此标记 **未验证完成**，不能写为构建通过，也不能把它归类为源码构建错误。
- 未启动完整 Compose 双服务，也未验证容器中的真实任务。后续应补齐环境文件并完成官方构建/启动，不能用本次诊断环境代替最终 Docker 可复现交付。

### 19.7 证据位置与复现说明

最终源码与诊断脚本位于 AgentHub 仓库之外。可用证据包括：

- `/home/abc/文档/ChatGPT/agenthub-recon-probe.py`：协议和模拟失败探针。
- `/home/abc/文档/ChatGPT/agenthub-recon-run-probe.py`：受控启动/验证/关闭脚本。
- `/home/abc/文档/ChatGPT/agenthub-recon-probe-final.log`：最终探针输出。
- `/home/abc/文档/ChatGPT/agenthub-recon-backend-controlled.log`：受控后端日志。
- `/home/abc/文档/ChatGPT/agenthub-recon-tests-bounded.log`：完整测试阻塞栈。
- `/home/abc/文档/ChatGPT/agenthub-recon-tests-ws.log`：独立 WebSocket 测试日志。
- `/home/abc/文档/ChatGPT/agenthub-recon-tests-core.log`：66 项测试通过记录。

这些是调查临时证据，不是 AgentHub 新功能或交付依赖。固定提交链接与本节命令用于长期复现。真实 LLM 执行仍需在后续受控验证中提供合法模型配置；本次未读取或输出任何真实凭据。

## 20. 源码证据索引

每条引用固定到调查提交。正文中的类/函数名对应这些源码；未链接的辅助文件也以第 0 节的源码根为基准。

[S01]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/entity/configs/node/node.py
[S02]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/entity/configs/node/agent.py
[S03]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/runtime/node/executor/agent_executor.py
[S04]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/workflow/graph.py
[S05]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/workflow/graph_manager.py
[S06]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/server/services/workflow_run_service.py
[S07]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/runtime/node/registry.py
[S08]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/runtime/node/agent/skills/manager.py
[S09]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/runtime/sdk.py
[S10]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/workflow/runtime/runtime_builder.py
[S11]: https://github.com/OpenBMB/ChatDev/tree/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/yaml_instance
[S12]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/server/bootstrap.py
[S13]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/server/routes/execute_sync.py
[S14]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/server/services/session_store.py
[S15]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/server/services/batch_run_service.py
[S16]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/server/services/websocket_manager.py
[S17]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/server/services/vuegraphs_storage.py
[S18]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/workflow/runtime/result_archiver.py
[S19]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/frontend/src/utils/apiFunctions.js
[S20]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/frontend/src/pages/LaunchView.vue
[S21]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/server/services/websocket_executor.py
[S22]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/schema_registry/registry.py
[S23]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/utils/logger.py
[S24]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/utils/token_tracker.py
[S25]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/tests/test_websocket_send_message_sync.py
[S26]: https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/pyproject.toml

定位方式示例：`rg -n 'class AgentNodeExecutor|def execute' runtime/node/executor/agent_executor.py`。基于固定 SHA 查源码时，优先按正文中的符号定位，避免后续 main 分支行号漂移。
