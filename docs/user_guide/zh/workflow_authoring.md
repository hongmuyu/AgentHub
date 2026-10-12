# 工作流编排指南

本指南聚焦 YAML 结构、节点类型、Provider 配置、边条件与模板导出，帮助工作流作者快速构建与调试 DAG。

## 1. 必备背景
- 熟悉 `yaml_instance/` 与 `yaml_template/` 的目录结构。
- 了解已注册节点类型（`agent`、`python`、`human`、`subgraph`、`passthrough`、`literal`、`loop_counter`、`loop_timer`）。
- 理解 `FIELD_SPECS`（见 [field_specs.md](field_specs.md)）与 Schema API（见 [config_schema_contract.md](config_schema_contract.md)）可被前端/IDE 用于动态表单。

## 2. YAML 顶层结构
工作流文件采用 `DesignConfig` 根结构，包含 `version`、`vars`、`graph`。下方为说明性节选；完整可用的工作流见 [net_example.yaml](../../../yaml_instance/net_example.yaml)，运行前需配置其 Provider：
```yaml
version: 0.4.0
vars:
  BASE_URL: https://api.example.com/v1
  API_KEY: ${API_KEY}
graph:
  id: paper_gen
  description: 文章生成与润色
  log_level: INFO
  is_majority_voting: false
  initial_instruction: |
    这是一个文章生成与润色流程，请输入一个词语或短句作为任务提示。
  start:
    - Article Writer
  end:
    - Article Writer
  nodes:
    - id: Article Writer
      type: agent
      config:
        provider: openai
        base_url: ${BASE_URL}
        api_key: ${API_KEY}
        name: gpt-4o
        params:
          temperature: 0.1
    - id: Human Reviewer
      type: human
      config:
        description: 请审阅文章，如接受结果请输入 ACCEPT 结束流程；否则输入修改意见。
  edges:
    - from: Article Writer
      to: Human Reviewer
    - from: Human Reviewer
      to: Article Writer
      condition:
        type: keyword
        config:
          none:
            - ACCEPT
          case_sensitive: false
```
- `version`：配置版本号，缺省为 `0.0.0`。当 `entity/configs/graph.py` 中的 Schema 发生破坏性调整时，用于与前端模板和迁移脚本对齐。
- `vars`：根级键值对，可在任意字段使用 `${VAR}` 占位，若未命中则回退到同名环境变量。`GraphDefinition.from_dict` 会拒绝在子图或节点下声明 `vars`，因此请仅在顶层维护。

**环境变量与 `.env` 文件**

系统支持在 YAML 配置中使用 `${VAR}` 语法引用变量。这些变量可用于配置中的任意字符串字段，常见用途包括：
- **API 密钥**：`api_key: ${API_KEY}`
- **服务地址**：`base_url: ${BASE_URL}`
- **模型名称**：`name: ${MODEL_NAME}`

系统在解析配置时会自动加载项目根目录下的 `.env` 文件（若存在）。变量解析的优先级如下：

| 优先级 | 来源 | 说明 |
| --- | --- | --- |
| 1（最高） | `vars` 中显式定义的值 | YAML 文件中直接声明的键值对 |
| 2 | 系统/Shell 环境变量 | 如通过 `export` 设置的值 |
| 3（最低） | `.env` 文件中的值 | 仅当环境变量尚未存在时生效 |

> [!TIP]
> `.env` 文件不会覆盖已存在的环境变量。这意味着您可以在 `.env` 中定义默认值，同时通过 `export` 或部署平台的环境变量配置来覆盖它们。

> [!WARNING]
> 若占位符引用的变量在上述三个来源中均未定义，配置解析时将抛出 `ConfigError` 并指明出错路径。
- `graph`：唯一必填段落，映射到 `GraphDefinition` dataclass。它包含：
  - **基础元信息**：`id`（必填）、`description`、`log_level`（默认 `DEBUG`）、`is_majority_voting`、`initial_instruction`、可选 `organization`。
  - **执行控制**：`start`/`end`（入口出口列表；系统会在启动时执行 `start` 中的节点）、`nodes`、`edges`。配置实现在 `entity/configs/node/` 与 `entity/configs/edge/` 中，Provider、模型、Tooling 配置位于 `node.config`。上例通过 `keyword` 条件在 `Human Reviewer -> Article Writer` 边上避免输入 `ACCEPT` 时继续循环。
  - **共享资源**：`memory`（定义 Memory store 列表，供 Agent 节点的 `config.memories` 引用）。调度器会校验节点引用是否在 `graph.memory` 中声明。
  - **Schema 参考**：`yaml_template/design.yaml` 会实时反映 `GraphDefinition` 字段，建议在修改后运行 `python -m tools.export_design_template` 或调用 Schema API 校验。

进一步阅读：[field_specs.md](field_specs.md)（字段说明）、[AgentHub 运行与可观测性](../../agenthub.md)、[yaml_template/design.yaml](../../../yaml_template/design.yaml)（生成的模板）。

## 3. 节点类型速览
| 类型 | 描述                                       | 关键字段 | 详细文档 |
| --- |------------------------------------------| --- | --- |
| `agent` | 调用 LLM，支持工具、记忆、thinking                  | `provider`, `name`, `role`, `tooling`, `thinking`, `memories` | [agent.md](nodes/agent.md) |
| `python` | 执行 Python 代码（脚本或指令），共享 `code_workspace/` | `interpreter`, `args`, `timeout_seconds`, `env` | [python.md](nodes/python.md) |
| `human` | 在 Web UI 阻塞等待人工输入                        | `description` | [human.md](nodes/human.md) |
| `subgraph` | 嵌入子 DAG，复用复杂流程                           | 来源 `type` 与 `config` | [subgraph.md](nodes/subgraph.md) |
| `passthrough` | 透传节点，默认只传递最后一条消息，可传递所有信息；用于上下文过滤和图结构优化   | `only_last_message` | [passthrough.md](nodes/passthrough.md) |
| `literal` | 被触发时输出固定文本消息，忽略输入                        | `content`, `role`（`user`/`assistant`） | [literal.md](nodes/literal.md) |
| `loop_counter` | 限制环路执行次数的控制节点                            | `max_iterations`, `reset_on_emit`, `message` | [loop_counter.md](nodes/loop_counter.md) |
| `loop_timer` | 限制环路执行时长的控制节点 | `max_duration`, `duration_unit`, `reset_on_emit`, `message` | [loop_timer.md（英文）](../en/nodes/loop_timer.md) |

详细字段可在前端使用 Schema API (`POST /api/config/schema`) 动态查询，也可参照 `entity/configs/` 中同名 dataclass。


## 4. Provider 与 Agent 设置
- 每个 `agent` 节点的 `config` 都要指定 `provider` 和模型 `name`；`AgentConfig` 将其作为必填字段。
- `name`、`api_key`、`base_url` 等字段支持 `${VAR}` 占位，便于跨环境复用。
- 不同 Agent 节点可分别指定 Provider；重试选项位于每个 Agent 的 `config.retry`。

### 4.1 Gemini Provider 配置示例
以下内容位于 `agent` 节点的 `config` 下：
```yaml
config:
  provider: gemini
  base_url: https://generativelanguage.googleapis.com
  api_key: ${GEMINI_API_KEY}
  name: gemini-2.0-flash-001
  input_mode: messages
  params:
    response_modalities: ["text", "image"]
    safety_settings:
      - category: HARM_CATEGORY_SEXUAL
        threshold: BLOCK_LOWER
```
Gemini Provider 支持多模态输入（图片/视频/音频会自动转换为 Part），并支持 `function_calling_config` 来控制工具调用行为。

## 5. 边与条件
- 基本边：
  ```yaml
  - source: plan
    target: execute
  ```
- 条件边：
  ```yaml
  edges:
    - source: router
      target: analyze
      condition:
        type: function
        config:
          name: should_analyze   # functions/edge/should_analyze.py
  ```
- 当 `condition` 抛错时，调度器会记录错误并抛出 `WorkflowExecutionError`，导致该分支（通常是整个运行）终止，后继节点不会继续执行。
- 通过注册中心可以声明更多条件类型，例如内置的 `keyword`（无需写 Python 函数）：

```yaml
edges:
  - from: review
    to: finalize
    condition:
      type: keyword
      config:
        any: ["FINAL", "APPROVED"]
        none: ["RETRY"]
        case_sensitive: false   # 默认为 true
```

`condition.type` 的合法值由后端注册中心（使用 `register_edge_condition` 注册）决定，schema 会自动在前端的下拉列表中展示 `summary` 描述。默认的 `function` 类型兼容旧写法（直接填写函数名字符串），未提供配置时等价于 `name: true`。

### 5.1 边级 Payload Processor

- 场景：当条件成立后希望“先处理一下消息”，例如根据正则提取得分、只保留结构化字段或者调用自定义函数对文本重写。
- YAML 字段：在任意边上新增 `process`，结构与 `condition` 相同（`type + config`），目前内置
  - `regex_extract`：基于 Python 正则。支持 `pattern`、`group`（名称或序号）、`mode`（`replace_content`、`metadata`、`data_block`）、`multiple`、`on_no_match`（`pass`/`default`/`drop`）等字段。
  - `function`：调用 `functions/edge_processor/*.py` 中的处理函数。函数签名为 `def foo(payload: Message, **kwargs) -> Message | None`。现在Processor 接口已标准化，`kwargs` 中包含了 `context: ExecutionContext`，可访问当前执行上下文。
- 运行时行为：
  - Processor 在条件通过且 `carry_data=true` 时执行，若返回 `None`，该边不会触发也不会向后继节点发送输入。
  - 日志中会在 `EDGE_PROCESS` 事件里显示 `process_label`、`process_type`，便于排查。
- 示例：
  ```yaml
  edges:
    - from: reviewer
      to: qa
      process:
        type: regex_extract
        config:
          pattern: "Score\\s*:\\s*(?P<score>\\d+)"
          group: score
          mode: metadata
          metadata_key: "quality_score"
          case_sensitive: false
          on_no_match: default
          default_value: "0"
  ```

## 6. Agent 节点高级特性
- **Tooling**：在 `AgentConfig.tooling` 中配置，具体见 [Tooling 模块](modules/tooling/overview.md)。
- **Thinking**：在 `AgentConfig.thinking` 中开启，如 `chain-of-thought`、`reflection`（详见 `entity/configs/node/thinking.py`）。
- **Memories**：`AgentConfig.memories` 绑定 `MemoryAttachmentConfig`，详见 [Memory 模块](modules/memory.md)。

## 7. 动态执行 (Map-Reduce/Tree)
`dynamic` 配置属于**边**，用于扩展目标节点。Map/Tree 配置、拆分策略和仓库内的边示例见 [Dynamic 执行指南](dynamic_execution.md)。

## 8. 设计模板导出
任意修改 Config/FIELD_SPECS 后，运行：
```bash
python -m tools.export_design_template \
  --output yaml_template/design.yaml \
  --mirror frontend/public/design_0.4.0.yaml
```
- 命令会读取注册表（节点、memory、tooling 等）与 `FIELD_SPECS`，自动生成 YAML 模板与前端镜像。
- 更新后请提交模板文件，并通知前端刷新静态资源。

## 9. CLI / API 运行
- **Web UI**：访问前端页面 → 选择 YAML → 填写运行参数 → 启动 → 在面板监控。**我们建议您采用此方式运行。**
- **HTTP**：先建立 `/ws` WebSocket 会话，再向 `POST /api/workflow/execute` 提交 `yaml_file`、`task_prompt`、当前 `session_id`、可选 `attachments` 和 `log_level`（`INFO` 或 `DEBUG`）。
- **CLI**：`python run.py --path yaml_instance/net_example.yaml --name test_run`，并在 CLI 提示中输入任务文本。

## 10. 调试建议
- 使用 Web UI 的上下文快照或 WareHouse 中的 `context.json` 检查节点输入输出。注意所有节点输出现已统一为 `List[Message]` 结构。
- 结合 [config_schema_contract.md](config_schema_contract.md) 的 breadcrumbs 功能，用 CLI `python run.py --inspect-schema` 快速查看字段定义。
- 若 YAML 占位符缺失，解析阶段会抛出 `ConfigError`，在 UI/CLI 中都可看到明确路径。
