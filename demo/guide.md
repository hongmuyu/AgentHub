# AgentHub 业务演示指南

本目录展示现有 AgentHub 的四个场景。[演示视频](agenthub_t48_demo.mp4) 为 **225 秒、1280×720、H.264 的真实屏幕录制**，不是模拟 API 响应的动画。视频包含一次新的 UI 提交、已完成真实模型运行的历史回放/查询、Registry UI 操作及[实测证据看板](t48_evidence_board.html)。看板只呈现已提交的 [T48 运行记录](t48_evidence_2026-10-09.json)，不是新的基准；四舍五入值以 JSON 为准。录制已裁掉地址栏，避免暴露会话 URL。

## 先通过页面运行

第一次使用请先完成 [README 部署步骤](../README.md#部署运行第一次请按顺序完成)，确认六个 Agent 已启用，再在 `/launch` 选择“AgentHub 任务”，从 `semantic` 开始。首次数据分析任务不需要联网检索工具。仅查看视频不需要运行下方复现脚本。

## 复现四个真实模型场景

前提是聊天服务可用，已配置与 T43 校准匹配的 DashScope Embedding 和 DeepSeek 重排，且六个示例 Agent 均已登记并启用，薄 Workflow 通过目录校验。配置步骤见[运行指南](../docs/agenthub.md#standard-docker-setup)与[可选重排](../docs/agenthub.md#optional-rerank)。

下面是主动发起的**真实模型调用**，会发送四类合成任务并在当前 SQLite 中新增五条 TaskRun，产生模型调用费用。它不是安装或日常启动的必需步骤。

```bash
docker compose up -d
curl -fsS --retry 12 --retry-delay 2 --retry-connrefused http://localhost:6400/health
docker compose exec -T backend python -m server.services.agenthub.demo_catalog
docker compose exec -T backend python demo/t48_reproduce.py --output /tmp/agenthub-t48-reproduction.json
docker compose cp backend:/tmp/agenthub-t48-reproduction.json /tmp/agenthub-t48-reproduction.json
```

修改过 `.env` 时先按运行指南重新创建容器。脚本在后端容器内部访问 `http://localhost:6400` 与 `ws://localhost:6400/ws`，最后一条命令将报告复制到宿主机 `/tmp`，不会覆盖仓库历史证据。

脚本要求初始六个 Agent 启用，会在场景四禁用 Data，并在 `finally` 中尝试恢复。如果调用或恢复失败，应先到 Registry 检查状态再重试。报告保存合成任务文本、Run ID、候选得分、所选 Agent、状态、原生完成/结果存在性及结果哈希，不保存响应正文、密钥或会话 ID。脚本本身不独立证明使用了哪份凭据；请私下核对配置，不显示密钥值。模型输出与选择可能变化，复现所得是新证据，不保证完全重复历史结果。

## 2026-10-09 的四场景记录

1. **清晰任务**：给定 LangGraph / AutoGen 材料，选择 Research，TaskRun 和 AgentRun 均为 `success`，有原生结果。Run 为 `7d623c0e-0624-4061-8e0b-13a9e1f1bbdd`；Research 的 cosine 为 0.497，路由约 1874 ms。
2. **模糊任务**：给定运维记录，语义 Top-K 同时包含 Data 和 Document；真实 DeepSeek 重排在候选内选择 Data，执行成功且有原生结果。Run 为 `ff655a2e-178d-4114-a5ef-2fee9828cc9f`，路由约 3538 ms。
3. **无匹配任务**：发送邮件请求返回 `NO_SUITABLE_AGENT`、`TaskRun.rejected`，没有选中 Agent 或创建 AgentRun。Run 为 `4e0e77c7-a0c4-469f-9af2-84bad07283cf`，最高 cosine 0.329，低于冻结阈值 0.357276…。
4. **目录变更**：同一中位数/极差任务先选择 Data 并成功，禁用 Data 后选择 Document 并成功，候选中不再出现 Data，随后恢复 Data。两条 Run 分别为 `d6d7d726-eaa9-4159-9417-b6e3adfe1657` 与 `3edd6519-df8a-4e1c-9700-04638ccf4f75`。

这五条新增运行使当时全局总数从 10 变为 15，success 从 5 变为 9，rejected 从 4 变为 5。Token 用量是**未知，不能按零统计**。更早的一条探索性外部检索运行在发出取消请求后，归档指标快照中仍显示 `running`；后续查询确认 Run `36ee4a9c-4285-4ee6-839b-a3363fc9cae9` 已为 `cancelled`。它不属于四场景验收，不能混入上述五条记录。

这里的业务 `success` 只表示选中 Agent 返回结构化成功结果且 Workflow 正常完成，不表示答案经过质量评估。拒绝是路由结果，与执行失败分开。

## 视频内容与回放边界

视频依次展示六 Agent Registry 与 Data 能力卡、清晰任务的路由和新提交、模糊候选及重排、无匹配拒绝、目录禁用/恢复前后对比，最后说明独立的 [T43 真实路由评测](../evaluation/t43_live_test_2026-10-08.json)。

新 UI 提交创建 Run `3d0c1f58-9a7c-499f-8de5-dbcb23b066f2`，录制后查询确认选中 Research，TaskRun / AgentRun 为 `success`，原生状态为 `completed`。它与四场景的五条验收 Run 分开记录。目录变更过程中没有修改 Router 源码；真实记录和受控假 Embedding / 临时 SQLite 的集成回归分别提供证据，不能把后者当成真实模型调用。

历史运行回放依赖当时仍存活的原生 WebSocket 会话。后端重启后可按 `run_id` 查询持久化记录，但旧会话和进行中的模型/工具调用不能恢复。T43 路由评测与这五条执行记录、[离线目录规模评测](../evaluation/t43_offline_catalog_size_2026-10-08.json)互相独立。

## 证据范围与限制

已展示业务 Registry、基于能力的语义发现/路由、候选内 LLM 重排、业务状态、Trace 和聚合指标，以及不修改 Router 分支的目录启停效果。T43 的 30 条真实路由测试中，Top-1 为 11/24 与 18/24，重排有两条截断错误，拒绝分母另行排除；测试集曾在调整重排输出上限后复用，K / 阈值保持冻结，详见[中文评测指南](../evaluation/guide.md)。

这些是有限范围的实现和实测证据，不是生产成功率、统计显著性或 SLA。外部工具结果、答案质量、并发上限、不可信公开多租户安全及运行中任务重启恢复均未由本演示验证。任务文本会持久化并发送给配置的 Embedding；重排还会发送任务和候选元数据。请使用允许发送给所选服务的数据，不要将密钥、私有数据库、附件或用户工件提交为演示证据。
