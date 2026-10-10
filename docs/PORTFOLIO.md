# AgentHub 项目材料（AI Agent 开发实习）

## 项目介绍

AgentHub 是基于 OpenBMB/ChatDev 2.0 的可信本地/内部 Agent 平台二次开发项目。面向企业研发与知识工作中的“用户不知道该选哪个 Agent”问题，我围绕业务 Agent 元数据建立版本化目录、能力语义发现、动态路由和可选 Top-K LLM 重排，并把选中 Agent 接入原有 Workflow/WebSocket Runtime。平台将业务 Run、路由决策和执行结果持久化，提供查询、指标、管理界面、评测与标准 Docker 复现。当前是六个受控示例 Agent 的 P0 原型，不代表真实企业部署或生产流量。

## 技术栈与工程工作

- **基础与服务**：Python、FastAPI、Pydantic、SQLite（WAL）、ChatDev Workflow/`WorkflowRunService`、WebSocket；前端沿用项目现有 Vue 页面与组件；Docker Compose 用于本地双服务交付。
- **语义与评测**：OpenAI-compatible embedding 接口、DashScope 1024 维 live 验证、精确 cosine Top-K、可选 DeepSeek rerank；确定性 fake embedding 用于单测，真实 provider 结果单列。
- **承担的工程范围**：梳理 ChatDev 运行链与边界，设计并实现 Registry/索引/Discovery/Router、受控 `runtime_ref`、业务 Run/Trace/AgentRun 与状态门禁；复用现有执行器并补充 API/UI、回归测试、calibration/test 分离的路由评测、Compose 验收和 Demo 证据。这里陈述的是本项目完成的工程工作，不声称团队规模或线上运营职责。

## 可用于简历的项目亮点

1. 基于 ChatDev 构建版本化 Agent Registry：元数据更新与向量写入在事务边界内完成，保留不可变版本快照；禁用 Agent 即从候选集排除。六个受控示例 Agent 可通过元数据与薄 workflow 加入目录，Router 无六类名称分支。
2. 实现 Embedding Discovery 与 Semantic Router：筛选 active、有效 workflow/索引的版本，对任务与公开能力文本计算 embedding，以精确 cosine 取 Top-K，再由 calibration 阈值决定选中或 `NO_SUITABLE_AGENT`。
3. 增加可选 Top-K LLM rerank 并做真实 provider 对照：60-case 数据集按 30 calibration / 30 independent test 分离；独立测试中 Top-1 从 `semantic` **11/24** 到 `semantic_llm` **18/24**。两条 DeepSeek 截断错误及延迟代价另行报告，不将该结果表述为执行或答案质量提升。
4. 将选中 Agent 的受控薄 workflow 接入既有 `WorkflowRunService` / WebSocket；用 TaskRun、RoutingTrace、AgentRun 记录业务状态、候选分数、版本、原生状态与安全错误码。正常 workflow 完成且目标 Agent 有结构化成功 outcome 才能记为 `success`，缺失 outcome、执行失败、拒绝与取消分开处理。
5. 实现轻量可观测性与可复现交付：SQLite 重启后可查询已完成 Run/Trace，metrics 提供成功/失败/拒绝的明确分母、路由与执行时延样本数、Agent 使用及未知 token 状态；标准 Compose、回归测试、T43 benchmark 和四场景视频构成交付证据。

## 核心实现与相对 ChatDev 的增量

- **Agent Registry / Embedding Discovery**：[Registry](../server/services/agenthub/registry.py)保存稳定 Agent ID、不可变版本和当前状态；[Discovery](../server/services/agenthub/discovery.py)只对合格版本计算相似度。公开元数据由 capability、name、description、tags、tools 构成；索引按 Agent/version/model key/metadata hash 关联。更新后重算向量，切换前校验；模型空间不一致不可混排。
- **Semantic Router / LLM Rerank**：[Router](../server/services/agenthub/router.py)先共享 semantic gate；`semantic` 选 Top-1，`semantic_llm` 只从已召回候选 ID 中选，不能引入目录外 Agent。无候选或低置信返回拒绝；embedding/rerank 基础设施错误返回失败，不冒充无匹配。
- **Workflow Execution / Observability**：[Dispatcher](../server/services/agenthub/workflow_dispatcher.py)二次校验选中版本和 allowlist workflow 后调用原生执行链；[状态转换](../server/services/agenthub/run_transitions.py)以结构化 outcome 与原生完成双条件提交终态。[业务指标](../server/services/agenthub/metrics.py)从持久记录聚合，Task Quality 无独立评测时为 `null`。
- **二次开发边界**：原 ChatDev 已有多 Agent Runtime、YAML Workflow、WebSocket 会话、工具、日志、附件与前端。AgentHub 新增的是业务目录、能力发现/路由、受控业务 Agent 到 workflow 的映射、独立 Run/Trace/指标及管理和评测入口；没有重写图执行器，也没有交付动态 Agent 团队或公开多租户平台。

## 可复核的数字与限制

- [T43 calibration](../evaluation/t43_live_calibration_2026-10-08.json)：36 clear / 12 ambiguous / 12 no-match；每半区 18/6/6，`K=5` 与阈值 `0.35727615782291194` 只用 calibration 确定。
- [T43 independent live test](../evaluation/t43_live_test_2026-10-08.json)：`semantic` 与 `semantic_llm` 的 Top-1 为 **11/24**、**18/24**；共同 Top-K recall **24/24**。全路由 p50/p95 分别为 **368.009/643.426 ms** 与 **2199.482/10066.879 ms**，各 30 条测量。后者有 **2 条**截断响应基础设施错误，仍计入延迟；no-match 拒绝准确率为 **3/6** 与 **3/4**，分母不同。真实测量只覆盖该配置与样本，不推断统计显著性或 SLA。
- [T43 live 合成目录规模报告](../evaluation/t43_live_catalog_size_2026-10-08.json)在 10/50/100 Agent 上用真实 provider 测顺序路由延迟；100 Agent 时 semantic p50/p95 **439.294/589.729 ms**，semantic_llm **3202.536/10776.190 ms**。它不衡量路由质量；[offline fake 报告](../evaluation/t43_offline_catalog_size_2026-10-08.json)不是 live 延迟。
- [T46 部署与测试记录](agenthub.md#verified-deployment-scope-and-limits)：标准双镜像/Compose、真实 provider 单路径及重启后查询已验证；Python 排除继承 WebSocket 夹具文件后 **629 passed、2 skipped**，前端 **42 passed**。完整 Python 套件 **NOT VERIFIED**。
- [T48 原始 Demo](../demo/t48_evidence_2026-10-09.json)：四个场景产生 **4 success、1 rejected** 的五条合成任务 Run。它既不是生产成功率，也不是任务答案质量；Task Quality 未独立评测，Demo token usage 为 unknown。

## 引用

- [GitHub P0 v1.0.0](https://github.com/hongmuyu/AgentHub/tree/v1.0.0) · [架构与部署](agenthub.md) · [评测方法和完整结果](../evaluation/README.md) · [Demo 复现说明](../demo/README.md)
- [T48 演示视频](https://github.com/hongmuyu/AgentHub/blob/v1.0.0/demo/agenthub_t48_demo.mp4) · [发布说明](RELEASE_NOTES.md)
