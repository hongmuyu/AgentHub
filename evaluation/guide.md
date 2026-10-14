# AgentHub 路由评测指南

本目录包含路由数据集、评测入口和历史结果。**fork 后运行项目不需要先执行评测**：先完成 [部署](../README.md#部署运行第一次请按顺序完成)或[本地开发](../docs/development.md)。本文用于理解指标、比较路由策略及后续修改路由时复现实验。已归档 JSON 对应各自标注的测量日期与配置。

## 1. 数据集与标签

数据集共 60 条合成标注任务：36 条清晰任务（clear）、12 条跨能力任务（ambiguous）、12 条无匹配任务（no-match）。校准集与测试集各 30 条，每份均为 18/6/6。校准用于选择 K 和拒绝阈值，测试用于比较已冻结配置下的两种策略。

`clear_cases_v1.json` 中每类示例能力有六条任务，三条校准、三条测试，标签使用逻辑 `runtime_ref`。`build_clear_dataset()` 将标签绑定到 `register_demo_catalog()` 返回的 UUID / 版本 / 状态；目录快照 ID 对公开元数据与状态取哈希。同一数据库重复登记不改变映射，新的数据库会生成新的身份和快照。

`ambiguous_no_match_cases_v1.json` 增加跨能力可接受引用集合及 `should_reject` 标签；`build_full_dataset()` 合并并校验完整数据。重复 ID / 文本会被拒绝。标签文件本身不是路由结果，也不代表任务答案质量。

### 清晰任务的人工语义审阅（2026-10-08，原 T39）

- Research：公共文档、规范、发布说明及技术对比，明确要求外部来源与引用，不假设访问私有资料；执行时仍依赖可用的联网工具。
- Code：提供 Python、JavaScript、SQL、TypeScript 片段，要求解释或修正，不假设能访问未提供的仓库或实际执行测试。
- Data：提供数值/表格，覆盖描述统计、变化、缺失/重复、异常点和比率，不依赖外部数据。
- Document：提供运维手册、规范、政策、笔记、清单或 API 说明，要求总结、提取或比较，不假设能获取私有文件。
- Planning：拆分未来工作、排序和设定里程碑，不承诺实际执行、人员安排或日期。
- Review：审阅已有设计、测试计划、摘要和草稿中的缺漏、无证据断言与质量风险。

每组任务在材料、操作或推理要求上有区别，不是同一句话的简单改写；任务文本及材料均为合成内容。

### 跨能力与无匹配任务的人工审阅（2026-10-08，原 T40）

六组跨能力各两条，一条校准、一条测试：Research + Document 比较给定摘要/API 说明；Code + Review 分析给定代码中的未处理输入；Data + Document 处理报告中的数值；Planning + Review 检查安排不合理的执行计划；Document + Review 比较互相冲突的草稿；Data + Review 核对数值与生成摘要的矛盾。

无匹配任务要求执行日历、邮件、SSO、部署、金融、HR、CRM、采购、楼宇控制、图像生成、音频转写或主动网络扫描等当前六个 Agent 不具备的操作。只给出行动计划不能视为完成这些请求。标签表示能力缺失，不表示模型/Embedding 故障。

## 2. 评测器与指标口径（原 T41 / T42）

`run_routing_benchmark()` 接收已校验的数据集、匹配的 Registry、指定 split、固定 Top-K、`CalibratedThreshold` 和策略。每条任务前后检查目录 UUID / 版本 / 状态，输出配置 ID、目录/数据快照、模型标识、阈值来源、策略、环境及逐条决策。

逐条结果包括 `selected`、`rejected` 或 `infra_error`，语义候选及原始 cosine 得分、选中 Agent、安全错误码和可选重排信息。耗时从 query Embedding 开始至路由决策返回，包含重排；如果在 query Embedding 开始前就返回，耗时为 `null`、`timing_scope=query_not_started`，不补造时长。当前评测器不执行 Workflow，不接受没有显式提供正文的附件任务，也不评价答案质量。

`calculate_routing_metrics()` 先校验快照、数据版本、split、配置 ID 及完整 case 覆盖，再统计：

- **Top-1 Accuracy**：在 clear / ambiguous 中，最终选中 Agent 是否属于可接受集合；应该路由却被拒绝计为错误。
- **Top-K Recall**：语义召回列表中是否出现任一可接受 Agent；即使使用 LLM 重排，也不改用重排顺序算召回。
- **Reject Accuracy / False Accept Rate**：对有效完成路由的 no-match，分别统计正确拒绝与错误选中。
- **基础设施错误**：单独列出 `infra_error_case_ids`，从所有质量比率分母中排除，不能当作正确拒绝或成功。每项输出分子、分母和值，空分母为 `null`。
- **延迟**：使用所有实际完成计时的调用，包括已计时的失败调用；列出错误样本和未计时样本。平均值为算术平均，p50/p95 使用排序后的最近秩 `ceil(percentile × sample_count)`。

离线小 fixture 的 selected / rejected / infra_error 和 fake reranker 只验证工程行为，不是实测目录质量。八条手算 fixture 中，五条应路由样例含一条基础设施错误，三条 no-match 含一条基础设施错误：有效 Top-1 为 2/4、Top-K 为 3/4、拒绝与误接受均为 1/2。七个有效计时为 10/20/30/40/50/60/70 ms，平均与 p50 为 40 ms、p95 为 70 ms；50 ms 的失败调用仅计延迟，另一条未开始计时的失败不虚构耗时。这些数字只用于说明公式。

## 3. 校准与冻结规则（原 T43）

`select_calibration_config()` 只接收同一目录、数据、模型和环境的 `semantic` 校准结果；拒绝空的应路由/no-match 分母、基础设施错误和重复候选配置。排序依次为：

1. 最大化 **Top-1 Accuracy + Reject Accuracy**。
2. 再比较 Top-K Recall。
3. 再优先较小 K、较高阈值。

Top-K Recall 不随拒绝阈值变化，不能单独用于选阈值。`FrozenRoutingConfig` 保存胜出配置 ID，`run_frozen_test()` 将同一 K / 阈值用于测试集两种策略。小型 fake fixture 验证这一边界，不代表 60 条任务的真实质量。

### 来源描述修正记录（2026-10-09）

M4 审阅发现生成器与两份历史校准文件的 `gate_selection_rule` 文案错误地写成先最大化 Top-K Recall + Reject Accuracy。实际执行算法一直是上面的 Top-1 + Reject 顺序；修正只改描述，没有改算法、原始测量或重新调用模型。

重新排序最终和 attempt1 各自保存的 183 项校准候选，均得到 `K=5`、阈值 `0.35727615782291194`。最终配置 ID 保持 `eaf560784ee8eb9eb4d00f51bc200bc514479caab129e56bf8f481cbf1cd1b4b`，attempt1 保持 `0111662eee1972a8e0c223e53e3d1a5530dfac84a9b463ac918d6df4a1e3957a`。选择过程没有使用测试标签。

历史 `provenance.source_sha256` 仍指向 2026-10-08 实际运行的生成器（提交 `166d7d8`），没有改成修正后的代码哈希。逐条结果、重排输出、耗时、split、配置 ID 和冻结门槛均保留；历史回归验证过校准冻结前的离线生成、保存网格的重新选择及排序规则。

## 4. 如何复现：先准备环境，再主动运行

使用[本地开发指南](../docs/development.md)中的 Python 3.12 环境，从仓库根目录运行。真实评测需要私有 `.env` 中的 DashScope / DeepSeek HTTPS 端点、模型配置及对应密钥；脚本校验提供方主机和密钥选择器。Workflow 只使用校验占位配置，不真正执行。

以下命令是可选评测，会产生新的结果，不能作为启动项目的前置步骤。真实评测会消耗模型 API，输出到 `/tmp`，避免覆盖仓库证据；需要长期保留时另行归档脱敏结果。

```bash
mkdir -p /tmp/agenthub-t43-reproduction

# 真实模型：先校准并冻结，再比较测试集两种策略。
.venv/bin/python -m evaluation.live_routing_benchmark --output-dir /tmp/agenthub-t43-reproduction

# 真实模型：10 / 50 / 100 个合成 Agent 的延迟。
.venv/bin/python -m evaluation.live_catalog_size_benchmark --output /tmp/agenthub-t43-reproduction/live_catalog_size.json

# 离线：假向量与本地重排，衡量本地工程开销。
.venv/bin/python -m evaluation.catalog_size_benchmark --output /tmp/agenthub-t43-reproduction/offline_catalog_size.json
```

前两条模型评测入口会加载根目录 `.env`，要求 `AGENTHUB_EMBEDDING_API_KEY_ENV=DASHSCOPE_API_KEY`、`AGENTHUB_RERANK_API_KEY_ENV=DEEPSEEK_API_KEY` 及各自真实密钥。真实与离线结果不能互相替代。新结果随模型、网络、缓存、硬件变化；先检查 `label`、样本数、错误、模型/配置指纹及环境，再做比较。新校准结果**不会自动更新 Web 应用使用的冻结文件**，模型迁移需单独审阅。

## 5. 历史真实路由结果（2026-10-08）

真实 DashScope Embedding 与 DeepSeek JSON 重排的可选冒烟检查在历史评测前通过。Embedding 标识为 `qwen-text-embedding-v4-1024`、维度 1024，重排模型为 `deepseek-flash`；报告只保存公开配置指纹，不包含密钥。

[校准文件](t43_live_calibration_2026-10-08.json)包含 30 条校准决策、183 组 K / 阈值候选、基线耗时、数据/目录/模型来源和最终配置。在测试前写入 `frozen_before_test`，只用校准标签与得分确定 `K=5`、阈值 `0.35727615782291194`。选中配置的校准得分：Top-1 15/24、Top-K 24/24、拒绝 4/6、误接受 2/6、基础设施错误 0。推导出的各配置没有虚构独立耗时，计时对应 30 条基线调用。

[测试文件](t43_live_test_2026-10-08.json)保留同一门槛下的每条选择、候选/得分、重排、错误及耗时：

| 策略 | 测试任务数 | Top-1 | 语义 Top-K | 拒绝准确率 | 误接受率 | 基础设施错误 | 路由 p50/p95（ms） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| semantic | 30 | 11/24 | 24/24 | 3/6 | 3/6 | 0 | 368.009 / 643.426 |
| semantic_llm | 30 | 18/24 | 24/24 | 3/4 | 1/4 | 2 | 2199.482 / 10066.879 |

两种策略均有 30 条实际耗时，包含计时的基础设施错误。重排两次错误为 no-match 上的 `RERANK_TRUNCATED_RESPONSE`，所以其拒绝/误接受分母是 4，不能直接与另一策略的 6 条分母混比；Top-1 的分母均为相同 24 条可匹配任务。

21 次实际 DeepSeek 调用的传输 p50/p95 为 2640.014/9703.747 ms；两策略共 60 次测试 query Embedding 的 p50/p95 为 334.196/642.572 ms。此前 [512-token 尝试](t43_live_test_attempt1_2026-10-08.json)及其[校准文件](t43_live_calibration_attempt1_2026-10-08.json)保留了 7 次截断；只将输出上限改为 2048 后复用测试集，截断降至 2 次，K / 阈值没有根据测试标签重新调整。不能称为严格一次性盲测，也不声称统计显著性或答案质量提升。

六 Agent 目录/索引构建为 2324.269 ms，与请求路由分开；校准 query Embedding 的 30 次调用 p50/p95 为 356.833/1251.049 ms。环境为 Python 3.12.13、Linux 6.8.0-138-generic、16 逻辑 CPU、AMD Ryzen 7 8745H；模型负载和远端缓存未受控。

## 6. 离线目录规模结果

[离线报告](t43_offline_catalog_size_2026-10-08.json)使用固定 SHA-256 种子、16 维假向量，每 Agent 一个启用且已索引的元数据版本，以及映射到静态校验薄 Workflow 的受控引用。没有 Workflow 或模型调用；阈值 -1.0 仅用于保持扫描路径开放，K=3。每个规模/策略预热 2 次，测量 36 次（12 条固定 query × 3），SQLite / OS 缓存保持热状态。

报告保存脚本哈希、硬件、Python/OS、建索引时间、样本数、错误及各段 p50/p95。本地重排保持原顺序，其耗时不是 LLM 延迟。下表单位均为 ms，是该机器的测量，不是生产目标。

| Agent 数 | 策略 | 全路由 p50/p95 | Query 到决策 p50/p95 | Discovery 不含 Embedding p50/p95 | Query Embedding p50/p95 | 本地重排 p50/p95 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 10 | semantic | 9.301 / 9.495 | 0.068 / 0.077 | 9.281 / 9.473 | 0.013 / 0.019 | 不适用 |
| 10 | semantic_llm | 9.456 / 9.834 | 0.106 / 0.120 | 9.400 / 9.773 | 0.013 / 0.014 | 0.025 / 0.028 |
| 50 | semantic | 44.964 / 46.811 | 0.211 / 0.239 | 44.942 / 46.790 | 0.013 / 0.025 | 不适用 |
| 50 | semantic_llm | 44.953 / 46.042 | 0.248 / 0.273 | 44.894 / 45.979 | 0.013 / 0.014 | 0.027 / 0.043 |
| 100 | semantic | 90.257 / 111.712 | 0.398 / 0.429 | 90.235 / 111.689 | 0.013 / 0.014 | 不适用 |
| 100 | semantic_llm | 90.437 / 96.474 | 0.437 / 0.549 | 90.376 / 96.414 | 0.013 / 0.020 | 0.028 / 0.037 |

每行 36 个样本，无路由错误；10/50/100 的目录构建时间分别为 36.343/181.746/361.029 ms，单独测量。`query-to-decision` 从 query Embedding 开始，不含前面的状态检查、Workflow 静态校验和索引加载；`full route` 包含这些步骤。Discovery 不含 Embedding 的耗时还包括精确 cosine 和排序，因此不能叫纯 cosine 扫描耗时。

亚毫秒 query-to-decision 只说明该 fixture 的向量计算开销较小，全路由成本仍随目录规模增长。结果不能单独证明精确扫描满足真实延迟目标，或向量数据库必然有收益；主要开销还包括重复校验和 SQLite 加载。P0 暂保留简单精确扫描，架构调整需要新的工作负载证据。

## 7. 真实模型目录规模结果

[真实规模报告](t43_live_catalog_size_2026-10-08.json)使用合成 Agent 元数据和查询、真实 1024 维 Embedding 与 DeepSeek 重排。每个规模/策略预热 1 次，测量 12 次；SQLite / OS / 模型端缓存未清空，K=3、阈值 -1 保持扫描和重排路径开放。只评延迟，不测 Workflow 或路由质量。单位均为 ms，重排耗时包含真实 API 请求。

| Agent 数 | 策略 | 全路由 p50/p95 | Discovery 不含 Embedding p50/p95 | Query Embedding p50/p95 | 重排 p50/p95 | 错误 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 10 | semantic | 318.952 / 617.735 | 16.241 / 18.297 | 303.130 / 601.340 | 不适用 | 0 |
| 10 | semantic_llm | 2178.088 / 3974.995 | 15.811 / 17.816 | 243.991 / 474.662 | 1917.320 / 3514.593 | 0 |
| 50 | semantic | 439.839 / 484.852 | 67.503 / 69.178 | 372.245 / 417.335 | 不适用 | 0 |
| 50 | semantic_llm | 3224.426 / 10302.801 | 68.963 / 73.226 | 377.345 / 826.630 | 2446.415 / 9883.576 | 1 次截断 |
| 100 | semantic | 439.294 / 589.729 | 131.137 / 135.468 | 306.658 / 461.633 | 不适用 | 0 |
| 100 | semantic_llm | 3202.536 / 10776.190 | 135.814 / 209.487 | 330.643 / 1593.711 | 2824.551 / 10334.756 | 2 次截断 |

非重排各段每行均为 12 个样本；`semantic_llm` 重排也包含全部 12 次实际调用（包括失败）。10/50/100 的目录构建时间分别为 3902.657/18904.271/36551.787 ms。报告另含 query-to-decision、每条错误下标/错误码、16 逻辑 CPU / 32,146,840 KiB 内存及源码哈希。

Discovery 不含 Embedding 的部分包含状态/白名单校验、SQLite 加载、cosine 和排序，没有单独测纯 cosine 时间。该机器上的本地部分随目录增长，而实际总延迟主要受 Embedding、尤其重排 API 影响。这些少量顺序调用既不是 SLA，也不证明并发性能；离线假向量数据必须与真实模型延迟和语义质量分开报告。
