# AgentHub 面试准备（P0 v1.0.0）

本稿基于 [P0 标签](https://github.com/hongmuyu/AgentHub/tree/v1.0.0) 的实现、[架构与部署记录](agenthub.md)、[T43 评测](../evaluation/README.md)和 [T48 Demo](../demo/README.md)。项目是可信本地/内部环境的原型。讲到结果时先说明分母、任务类型和证据范围。

## 1. 三分钟项目介绍稿

> AgentHub 是我在 ChatDev 2.0 上做的 Agent 平台二次开发。原项目已经有 YAML Workflow、多 Agent Runtime、WebSocket 会话、工具与前端，但业务用户仍要知道具体 workflow。我的目标是让用户提交自然语言任务后，平台从注册的 Agent 能力中发现候选，选出一个 Agent，并沿原执行链运行和记录结果。
>
> 我先做版本化 Registry。每个业务 Agent 有稳定 ID、公开能力元数据、状态和受控的逻辑 runtime_ref。注册或更新时，对能力文本生成 embedding，并把向量与 Agent 版本绑定。路由时先排除 disabled、workflow 无效或索引不匹配的版本，再做精确 cosine Top-K。校准阈值以下直接返回 `NO_SUITABLE_AGENT`；如果启用 LLM，只把已经召回的候选交给它重排，不允许它编造 Agent。
>
> 选中后，服务端把 runtime_ref 解析成受控薄 workflow，再调用 ChatDev 现有的 `WorkflowRunService` 和 WebSocket 执行链。我另外保存 TaskRun、RoutingTrace、AgentRun。HTTP 接受请求或原生 workflow 标记 completed 都不算业务成功；必须拿到目标 Agent 的结构化 `succeeded` outcome，而且没有失败或取消信号。拒绝、基础设施失败、缺失 outcome、取消分别入库。Run 查询和 metrics 会展示候选分数、版本、状态、时延以及未知 token 值。
>
> 我用 60 条标注路由任务做评测，30 条校准、30 条独立测试；冻结 `K=5` 和阈值后，真实 DashScope embedding 加 DeepSeek 重排的独立测试 Top-1 是 semantic 11/24、semantic+LLM 18/24。但全路由 p50/p95 从 368/643 ms 变成 2199/10067 ms，LLM 路径还有两次截断响应错误。这只是一次小样本路由质量比较，不是执行成功率。标准 Compose 验收、重启后查询和四场景视频展示了端到端能力；五条 Demo Run 是 4 success、1 rejected，答案质量仍未独立评测。

## 2. 五分钟技术架构讲解稿

> 从入口看，用户在 Launch 页选择 AgentHub task，前端先建立真实 WebSocket session，再向 `POST /api/agenthub/tasks` 发送任务、session ID、附件 ID 和策略。服务创建有独立 `run_id` 的 TaskRun。session ID 管原生执行连接，run ID 管平台业务记录，两者不能混用。原 ChatDev Manual YAML 路径仍可直接执行。
>
> Registry 把稳定 Agent ID 与当前版本、状态分开存储。版本内容不可变；元数据更新会生成新版本 embedding，事务内写入并切换当前版本。它避免旧 Run 因后续元数据修改而无法解释。`runtime_ref` 是逻辑引用，必须在服务端 manifest 中命中一个受控、经过验证的薄 workflow，客户端不能通过它传任意文件路径。六个演示 Agent 只是普通目录项。
>
> Discovery 读取 active 的当前版本，验证 workflow 目标和模型空间一致的索引，过滤缺失或 metadata hash 不匹配的向量。它把 task 编码为同维向量，逐个计算精确 cosine，稳定排序返回 Top-K。Router 对候选最高分应用事先校准的阈值；没有合格候选就拒绝。`semantic` 选第一项；`semantic_llm` 在同一个 semantic gate 之后让 DeepSeek 只对 Top-K 候选 ID 排序。embedding/rerank 出错归为路由失败，不归为业务无匹配。
>
> Dispatcher 在启动前再次验证选中版本仍 active、快照未变化，并重新解析薄 workflow。之后复用 `WorkflowRunService.start_workflow()` 与 WebSocket 执行，不改图调度器。目标 Agent 节点通过 recorder 报告带 run ID 和 node ID 的结构化 outcome。原生 session 完成且该 outcome 为 `succeeded`，状态服务才在事务内把 TaskRun 和 AgentRun 置为 success；outcome 缺失或无效为 failed，原生 error 为 failed，确认取消才是 cancelled。RoutingTrace 留下候选、cosine、阈值、重排和选择；拒绝没有 AgentRun。
>
> 业务记录放在独立 SQLite WAL 文件中，日志和大结果仍用 ChatDev 的机制。查询 API 与 metrics 聚合 Run 状态、策略、Agent 使用、路由/执行延迟及 token 已知/未知数。重启后能读已完成 Run，但内存 WebSocket session 和进行中的 provider 调用不能恢复。这个边界决定了 P0 适用于单后端进程与低并发的本地部署，不能宣传为分布式运行平台。
>
> 评测上先用 60-case 数据集的一半选 K/阈值，冻结后测另一半。测试 Top-K recall 两策略均 24/24，Top-1 是 11/24 和 18/24；30 条路由样本的 p50/p95 分别约 368/643 ms 与 2199/10067 ms，后者两条 no-match 发生截断错误，故拒绝准确率分别是 3/6 和 3/4。另做 10/50/100 的合成目录 live 延迟测试，说明精确扫描在这次小目录、顺序请求条件下可用，但没有并发 SLA 或答案质量结论。标准 Docker、629 passed/2 skipped 的 Python 子集、42 passed 前端测试和四场景 Demo 是交付证据；完整 Python 套件因继承 WebSocket fixture 阻塞，保持 NOT VERIFIED。

## 3. 架构及核心执行链

```text
Launch + existing WebSocket session
  → POST /api/agenthub/tasks → TaskRun(pending)
  → active/current Registry + valid runtime_ref/index
  → task embedding → exact cosine Top-K → calibrated threshold
  → semantic Top-1 | semantic_llm rerank within Top-K | NO_SUITABLE_AGENT
  → RoutingTrace → server-owned manifest → validated thin workflow
  → WorkflowRunService → native WebSocketGraphExecutor
  → target AgentExecutionOutcome + native session state
  → AgentRun/TaskRun terminal transition → query API + metrics
```

关键代码：[Registry](../server/services/agenthub/registry.py)、[Discovery](../server/services/agenthub/discovery.py)、[Router](../server/services/agenthub/router.py)、[任务服务](../server/services/agenthub/task_service.py)、[Dispatcher](../server/services/agenthub/workflow_dispatcher.py)、[状态转换](../server/services/agenthub/run_transitions.py)、[指标](../server/services/agenthub/metrics.py)。

## 4. 关键设计取舍

**为什么 SQLite，不用 PostgreSQL？** P0 是单后端进程、单机 Compose、小目录和轻量查询；SQLite 的事务、唯一约束、WAL、busy timeout 与持久文件足以支撑当前验收，少一个数据库服务。它不证明高并发写或多副本能力；若进入多实例场景，应基于实际负载和运维要求评估 PostgreSQL，并迁移 Agent/version/run/trace 关系。[设计依据](../AGENTHUB_DESIGN.md#6-持久化决策)。

**为什么 Exact Cosine Scan，不用 Qdrant？** 当前只对 active、可解析且索引有效的小目录做线性精确扫描。10/50/100 合成目录 live 测量中，100 Agent 的 discovery（不含 query embedding）p50/p95 为 semantic **131.137/135.468 ms**；它包括状态/allowlist 校验、SQLite 加载、cosine 与排序，不能声称纯 cosine 只花这么久。该顺序样本下 embedding 与 rerank 的外部调用也占显著时间；暂无专用向量库的实测必要性。若规模或并发上升，应测召回与延迟，再考虑 ANN/Qdrant。[规模证据](../evaluation/t43_live_catalog_size_2026-10-08.json)。

**Semantic 与 Semantic + LLM 的效果/延迟如何权衡？** 两者共享 `K=5` 和拒绝阈值。30 条独立测试中，24 条适用匹配任务的 Top-1 是 **11/24** 对 **18/24**，Top-K recall 均 **24/24**。全路由 p50/p95 是 **368.009/643.426 ms** 对 **2199.482/10066.879 ms**，各 30 样本；LLM 路径还发生两次截断响应基础设施错误。故 LLM 可作为可选策略，并须展示延迟/错误成本。单次小样本不能证明统计显著性、生产收益或任务答案质量。[测试原始记录](../evaluation/t43_live_test_2026-10-08.json)。

**为什么版本化 Registry？** Agent 身份与当前内容分开，更新时写不可变版本快照和对应 embedding，再原子切换；Run/Trace 记录选中版本，便于解释历史决策。状态启停独立于内容版本；重新启用前验证 workflow 与索引。更新失败不暴露半成品版本。[实现](../server/services/agenthub/versions.py)。

**如何处理异常和取消？** 无候选/低阈值是 `rejected` + `NO_SUITABLE_AGENT`，不创建 AgentRun；embedding、rerank 或 dispatch 错误是 `failed`，保存安全错误码；provider/Workflow 执行错误由原生 error 或目标 Agent failed outcome 导向 `failed`；原生 completed 但缺少目标 outcome 是 `EXECUTION_OUTCOME_MISSING`，无效/错节点为 `EXECUTION_OUTCOME_INVALID`；只有原生路径确认取消才提交 `cancelled`。单纯发出取消请求或 WebSocket 断连不等于取消成功。终态不能被后来的成功覆盖。[路由](../server/services/agenthub/router.py) · [执行](../server/services/agenthub/workflow_dispatcher.py) · [状态门禁](../server/services/agenthub/run_transitions.py)。

**为什么 Workflow Completed 不等于业务 Success？** workflow 完成只说明图执行收束，不保证目标业务 Agent 正确运行。AgentHub 要求原生 `completed`、目标 `run_id`/`node_id` 对应的结构化 `succeeded` outcome、且无失败/取消信号；缺一即不能提交 `success`。这仍只是执行成功，不代表答案正确。Task Quality 需要独立断言、rubric 或人工评估，目前未测量。

**为什么复用 ChatDev？二次开发了什么？** ChatDev 已提供 YAML 图执行、节点/Provider/Tool、WebSocket、日志、附件与交互前端。AgentHub 在应用服务边界增加业务 Agent Registry、Embedding Discovery、路由/拒绝、受控 runtime_ref、业务状态/Trace/metrics、管理界面、评测和部署材料。它没有重写 `DAGExecutor`，没有把模型名或节点类型当 Agent 身份，也没有做动态团队。[架构说明](agenthub.md#architecture-and-boundaries)。

## 5. 常见追问与参考回答

1. **为什么不能直接让 LLM 看全量 Agent？** P0 先用能力向量召回、状态与 workflow 校验收窄候选；LLM 只排序候选 ID，限制越界选择，并能单独比较语义与重排成本。
2. **新增第七个 Agent 要改 Router 吗？** 不需要名称分支；需注册合法元数据、提供服务端允许的薄 workflow/manifest 映射并建立 embedding，随后走同一 Discovery/Router。[目录与 manifest](agenthub.md#architecture-and-boundaries)。
3. **cosine 分数是概率吗？** 不是；它是相似度。约 0.357 的阈值来自 calibration split，不能解释为“35.7% 的成功概率”。
4. **为什么 Top-K recall 24/24 而 Top-1 低？** 正确 Agent 已被召回，但排序第一不总是标签目标；这正是可选 rerank 的空间。这里只针对 24 条适用匹配任务和六 Agent 目录。
5. **阈值和 K 怎样避免测试泄漏？** 用 30 条 calibration 的候选/标签选出 `K=5`、阈值 `0.35727615782291194`，写入冻结 artifact 后，再运行 30 条 independent test；test 标签不用于重调这两个参数。[校准 artifact](../evaluation/t43_live_calibration_2026-10-08.json)。
6. **为什么 LLM 拒绝准确率的分母是四？** 六条 no-match 中两条 DeepSeek 响应截断，属于基础设施错误，不能算正确拒绝或误接受；其余四条有 3 条正确拒绝。两条错误仍保留在 30 条延迟样本中。
7. **Provider 故障会回退成 semantic 吗？** 当前 P0 没有自动 fallback 链；embedding/rerank 故障以 `failed` 和错误码记录，不伪装成 semantic 路由或 no-match。业务可显式选择 `semantic` 策略。
8. **如何防止 LLM 选目录外 Agent？** Router 校验 RerankAdapter 返回的候选 ID 非空、无重复且都在 semantic Top-K 中；无效返回为 `RERANK_INVALID_RESPONSE`，不会执行任意 Agent。[Router](../server/services/agenthub/router.py)。
9. **元数据更新途中 embedding 失败会怎样？** 新向量先生成并校验，然后在事务中写版本与索引、切当前版本；失败时不切换，旧版本仍可用。[Registry](../server/services/agenthub/registry.py)。
10. **disabled Agent 的旧记录会消失吗？** 不会；当前状态使其退出新请求的候选集，旧版快照与已持久化 Run/Trace 仍可查询。T48 对同一任务在禁用前后记录 Data→Document 的变化。[Demo 记录](../demo/t48_evidence_2026-10-09.json)。
11. **runtime_ref 为什么不能直接存绝对路径？** P0 用逻辑引用和服务端 manifest 限制可执行面，启动前还重校验选中快照与 workflow；让客户端给任意路径会越过目录治理。[Resolver](../server/services/agenthub/runtime_resolver.py)。
12. **HTTP 202 表示任务成功了吗？** 只表示提交已接受/派发。业务终态要查 `run_id`；原生 session 和 TaskRun 是不同生命周期。[任务 API](../server/routes/agenthub_tasks.py)。
13. **如何保证 completed 不误记为 success？** Dispatcher 读取目标 recorder；缺失、错 run/node 或 failed outcome 都不能成功。状态转换再验证 completed + `succeeded` 且无错误/取消，事务性提交终态。[状态门禁](../server/services/agenthub/run_transitions.py)。
14. **用户取消与断线怎么区分？** 只有原生执行路径确认 `SessionStatus.CANCELLED` 才写业务 `cancelled`；请求发出或 WebSocket 断连本身不足以证明执行停止。[Dispatcher](../server/services/agenthub/workflow_dispatcher.py)。
15. **重启后能恢复什么？** SQLite 中已完成的 Agent、Run、Trace、AgentRun 和 metrics 可再查；正在运行的原生 WebSocket session、provider/tool 调用不续跑。T46 验证的是前者。[部署边界](agenthub.md#verified-deployment-scope-and-limits)。
16. **Token usage 为零吗？** T48 记录的 Token usage 为 unknown，不能按 0 统计。metrics 分开保存 known_count/unknown_count，不能把未返回的 provider 用量补为 0。[指标实现](../server/services/agenthub/metrics.py)。
17. **性能数据能证明 100 Agent 的生产 SLA 吗？** 不能。合成目录每档每策略 12 条顺序请求，有预热、缓存与远端负载不受控；本次数字仅描述该机器和 provider 组合，没有并发或尾延迟 SLA 验证。
18. **测试是否全部通过？答案是否正确？** T46 的 Python 子集为 629 passed/2 skipped、前端 42 passed；完整 Python 套件受继承 WebSocket fixture 阻塞，仍 NOT VERIFIED。T48 五条 Demo Run 的业务状态为 4 success/1 rejected，Task Quality 没有独立评测，不能说“答案准确率 100%”。

## 6. 证据速查与表达边界

- 路由质量与延迟：[T43 calibration](../evaluation/t43_live_calibration_2026-10-08.json)、[independent test](../evaluation/t43_live_test_2026-10-08.json)、[live 合成目录](../evaluation/t43_live_catalog_size_2026-10-08.json)。offline fake benchmark 单独见 [offline 报告](../evaluation/t43_offline_catalog_size_2026-10-08.json)。
- 部署/测试：[AgentHub 验收说明](agenthub.md#verified-deployment-scope-and-limits)；完整 Python 套件的继承阻塞见 [开发基线](../DEVELOPMENT_BASELINE.md)。
- 业务 Demo：[视频](https://github.com/hongmuyu/AgentHub/blob/v1.0.0/demo/agenthub_t48_demo.mp4)、[脚本与说明](../demo/README.md)、[五条 Run 原始记录](../demo/t48_evidence_2026-10-09.json)。
- 避免的表述：真实企业用户、生产流量、统计显著提升、生产 SLA、完整 Python suite 通过、任务答案质量成功率、进程重启续跑。当前证据均未支持这些结论。
