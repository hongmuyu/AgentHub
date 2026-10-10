# AgentHub P0 v1.0.0 Release Notes

发布日期：2026-10-10。版本基线为 annotated tag [`v1.0.0`](https://github.com/hongmuyu/AgentHub/tree/v1.0.0)，指向 `main` 的 M4 合并提交 `847f973185807f6e074092199c692cd43ea1a804`。本文是发布后的说明文档，单独提交到 `agenthub-dev`，不属于该标签的文件快照。

## 功能与架构

- 在 ChatDev 2.0 上增加业务 Agent Registry：元数据注册、更新、查询、搜索、启用/禁用，保留不可变版本快照与版本对应的 embedding。随附六个企业研发/知识工作示例 Agent；它们是受控目录和薄 workflow，不是硬编码的 Router 类型。
- 对当前 active、workflow 可解析且索引有效的 Agent 做 capability embedding discovery、精确 cosine 排序和 Top-K 召回；由校准阈值决定选中或 `NO_SUITABLE_AGENT`。可选 `semantic_llm` 仅重排同一 Top-K，不创建动态 Agent 团队。
- 用服务端 manifest 把逻辑 `runtime_ref` 映射到经过验证的薄 workflow，再复用 `WorkflowRunService`、WebSocket 执行、日志、附件与取消路径。原有 Manual YAML 入口保留。
- 独立 SQLite（WAL）持久化 TaskRun、RoutingTrace、AgentRun。业务状态与原生 session 状态分开；只有正常 workflow 完成且目标 Agent 给出匹配的结构化 `succeeded` outcome 才能记为业务成功。提供运行查询、轻量指标 API、任务入口及 Registry 管理界面。

架构与操作细节见 [AgentHub 文档](agenthub.md)、[冻结设计](../AGENTHUB_DESIGN.md) 和 [P0 任务清单](../TASKS.md)。

## 部署与兼容性

T46 在标准 backend/frontend 镜像及 Compose 配置下完成构建、启动、HTTP/WebSocket、旧 Workflow/Manual YAML 与 AgentHub 入口检查；六个示例 Agent 通过真实 embedding 注册。一条真实 provider 支持的任务选中 Data Agent，并在结构化 outcome 与原生完成后进入业务 `success`。重启 backend 后，已完成 Run、Trace、AgentRun、Registry 和 metrics 仍可查询。[部署步骤及验收边界](agenthub.md#verified-deployment-scope-and-limits)。重启可查询不代表中途的 provider/tool 调用或原生 WebSocket session 能续跑。

## 测试与 Benchmark

- T46 记录的较宽 Python 测试集排除继承的 `tests/test_websocket_send_message_sync.py` 后为 **629 passed、2 skipped**；前端为 **42 passed**。完整 Python 套件仍为 **NOT VERIFIED**：继承 WebSocket `MagicMock` 测试夹具进入重连/序列化循环。[基线说明](../DEVELOPMENT_BASELINE.md)；这些结果不应表述为完整测试全绿。
- T43 的 60 条标注路由任务分为 36 clear、12 ambiguous、12 no-match；30 条 calibration 用于选择 `K=5` 和 cosine 阈值 `0.35727615782291194`，另外 30 条独立 test 在冻结配置后运行。真实 DashScope embedding / DeepSeek rerank 测试中，两策略各有 24 条适用匹配任务：`semantic` Top-1 **11/24**，`semantic_llm` **18/24**；共同的 semantic Top-K recall 均为 **24/24**。
- 同一独立测试的全路由 p50/p95：`semantic` **368.009 / 643.426 ms**；`semantic_llm` **2199.482 / 10066.879 ms**。每种策略各 30 条延迟样本。LLM 策略的两个 no-match 请求因 DeepSeek 输出截断成为 `RERANK_TRUNCATED_RESPONSE` 基础设施错误，仍计入延迟；拒绝准确率为 `semantic` **3/6**、`semantic_llm` **3/4**，错误样本不进入后者质量分母。21 次实际 rerank 调用的 transport p50/p95 为 **2640.014 / 9703.747 ms**。见 [独立测试原始记录](../evaluation/t43_live_test_2026-10-08.json) 与 [校准记录](../evaluation/t43_live_calibration_2026-10-08.json)。
- 独立的 10/50/100 Agent **合成目录** live-provider 延迟测试，每档每策略一轮预热及 12 次测量，使用 1024 维真实 embedding、真实 DeepSeek，`K=3`、threshold=-1。100 Agent 时全路由 p50/p95 为 `semantic` **439.294 / 589.729 ms**、`semantic_llm` **3202.536 / 10776.190 ms**。它只测该环境下顺序请求的延迟，不测路由准确率或并发吞吐。[live 规模报告](../evaluation/t43_live_catalog_size_2026-10-08.json) 与使用 fake 16 维 embedding/本地 identity rerank 的 [offline 工程报告](../evaluation/t43_offline_catalog_size_2026-10-08.json)须分开解读。

以上是单次有限样本测量，不构成统计显著性、SLA 或任务执行成功率结论。完整方法、硬件和缓存条件见 [evaluation/README.md](../evaluation/README.md)。

## Demo 与限制

[四场景 Demo 视频](https://github.com/hongmuyu/AgentHub/blob/v1.0.0/demo/agenthub_t48_demo.mp4)、[复现脚本与说明](../demo/README.md)、[原始 Run 记录](../demo/t48_evidence_2026-10-09.json)展示清晰任务、跨能力 rerank、无匹配拒绝，以及禁用 Data Agent 前后同一任务由 Data 改选 Document。四个场景共产生 **5 条合成任务 Run：4 success、1 rejected**；这是 Demo 的业务状态记录，不是生产业务成功率。Task Quality 未经独立断言、rubric 或人工评测，指标为 `null`；Demo token usage 亦为 unknown。

P0 面向可信本地/内部环境，未提供公开多租户认证/RBAC。外部工具/MCP 结果、其他 provider 组合、并发负载上限、进行中 Run 的进程重启恢复和任务答案质量均 **NOT VERIFIED**。任务文本会持久化并送至配置的 embedding provider；启用 rerank 时，任务文本和公开候选元数据还会送至 rerank provider。请勿将私有 `.env`、SQLite 数据、附件或运行日志加入版本库。
