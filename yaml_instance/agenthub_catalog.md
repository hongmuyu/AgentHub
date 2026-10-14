# AgentHub 示例 Agent 目录

`agenthub_*_metadata.json` 中的六个条目是普通 Registry 元数据。每个 `runtime_ref` 通过 `agenthub_manifest.json` 解析到一个完整的单 Agent 薄 Workflow，直接接收用户任务，不依赖上游团队输出。先完成 [Docker 部署与私有配置](../docs/agenthub.md#standard-docker-setup)，再使用独立配置的 AgentHub Embedding 服务登记：

```bash
docker compose exec -T backend python -m server.services.agenthub.demo_catalog
```

命令输出每个引用的真实 `agent_id` 和版本。元数据未变时重复登记保留原映射及启停状态；已有身份或内容冲突时停止，不会静默覆盖或重复注册。宿主机直接运行需要先加载 `.env`，见[本地开发指南](../docs/development.md#4-登记目录并开始使用终端-c)。

## 六个示例的能力边界

- **Research**：在配置的联网工具可用时搜集公开资料；外部访问能力不由登记成功保证。
- **Code**：基于用户提供的代码解释行为、分析问题或给出修改建议。
- **Data**：分析用户提供的结构化数值、统计与趋势。
- **Document**：提取、总结和比较用户提供的文档内容。
- **Planning**：分解未来工作，安排步骤、优先级和里程碑。
- **Review**：检查已有成果的错误、缺漏和质量问题。

除 Research 外，五个示例没有外部工具，工作依据是任务中提供的材料。Code 与 Review 在代码质量上有交集，但前者侧重解释/调试，后者侧重审查现有成果；Research 可检索公开来源，Document 留在给定文本范围；Planning 安排后续工作，Review 检查当前方案；Data 可计算文档内的数值，Document 提取和比较文档中的陈述。

这些有意保留的重叠与边界是路由输入，不保证答案质量或模型实时可用。六个示例不是平台硬编码支持的六种类型；新增兼容 Agent 的步骤见[开发指南](../docs/development.md#6-新增一个兼容的示例-agent)。
