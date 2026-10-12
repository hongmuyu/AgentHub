# Workflow Authoring Guide

This guide covers YAML structure, node types, provider configuration, edge conditions, and design template export so you can build and debug ChatDev workflows. The Chinese version is [here](../zh/workflow_authoring.md).

## 1. Prerequisites
- Know the layout of `yaml_instance/` and `yaml_template/`.
- Understand the registered node types (`agent`, `python`, `human`, `subgraph`, `passthrough`, `literal`, `loop_counter`, `loop_timer`).
- Review `FIELD_SPECS` (see [field_specs.md](field_specs.md)) and the Schema API contract ([config_schema_contract.md](config_schema_contract.md)) if you rely on dynamic forms in the frontend/IDE.

## 2. YAML Top-level Structure
Workflow files use the `DesignConfig` root with `version`, `vars`, and `graph`. The following is an illustrative excerpt; for a complete checked-in workflow use [net_example.yaml](../../../yaml_instance/net_example.yaml) with its required provider configuration:
```yaml
version: 0.4.0
vars:
  BASE_URL: https://api.example.com/v1
  API_KEY: ${API_KEY}
graph:
  id: paper_gen
  description: Article generation and refinement
  log_level: INFO
  is_majority_voting: false
  initial_instruction: |
    Provide a word or short phrase and the workflow will draft and polish an article.
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
        description: Review the article. Type ACCEPT to finish; otherwise provide revision notes.
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
- `version`: optional configuration version (defaults to `0.0.0`). Increment it whenever schema changes in `entity/configs/graph.py` require template or migration updates.
- `vars`: root-level key-value map. You can reference `${VAR}` anywhere in the file; fallback is the same-name environment variable. `GraphDefinition.from_dict` rejects nested `vars`, so keep this block at the top only.

**Environment Variables & `.env` File**

The system supports referencing variables in YAML configurations using `${VAR}` syntax. These variables can be used in any string field within the configuration. Common use cases include:
- **API keys**: `api_key: ${API_KEY}`
- **Service URLs**: `base_url: ${BASE_URL}`
- **Model names**: `name: ${MODEL_NAME}`

The system automatically loads the `.env` file from the project root (if present) when parsing configurations. Variable resolution follows this priority order:

| Priority | Source | Description |
| --- | --- | --- |
| 1 (highest) | Values defined in `vars` | Key-value pairs declared directly in the YAML file |
| 2 | System/shell environment variables | Values set via `export` or system config |
| 3 (lowest) | Values from `.env` file | Only applied if the variable doesn't already exist |

> [!TIP]
> The `.env` file does not override existing environment variables. This allows you to define defaults in `.env` while overriding them via `export` or deployment platform configurations.

> [!WARNING]
> If a placeholder references a variable that is not defined in any of the three sources above, a `ConfigError` will be raised during configuration parsing with the exact path indicated.
- `graph`: required block that maps to the `GraphDefinition` dataclass:
  - **Metadata**: `id` (required), `description`, `log_level` (default `DEBUG`), `is_majority_voting`, `initial_instruction`, and optional `organization`.
  - **Execution controls**: `start`/`end` entry lists (the system executes nodes listed in `start` at the beginning), plus `nodes` and `edges`. Provider/model/tooling settings live inside each `node.config`. In the example the `keyword` condition on `Human Reviewer -> Article Writer` keeps looping unless the reviewer types `ACCEPT`.
  - **Shared resources**: `memory` defines stores available to `node.config.memories`. The validator ensures every attachment points to a declared store.
  - **Schema references**: `yaml_template/design.yaml` mirrors the latest `GraphDefinition` shape. After editing configs run `python -m tools.export_design_template` or hit the Schema API to validate.

Further reading: [field_specs.md](field_specs.md) (field catalog), [AgentHub runtime and observability](../../agenthub.md), and [yaml_template/design.yaml](../../../yaml_template/design.yaml) (generated baseline template).

## 3. Node Type Cheatsheet
| Type | Description                                                                                                                                                                  | Key fields | Detailed Docs |
| --- |------------------------------------------------------------------------------------------------------------------------------------------------------------------------------| --- | --- |
| `agent` | Runs an LLM-backed agent with optional tools, memories, and thinking phases.                                                                                                 | `provider`, `name`, `role`, `tooling`, `thinking`, `memories` | [agent.md](nodes/agent.md) |
| `python` | Executes Python scripts/commands sharing the `code_workspace/`.                                                                                                              | `interpreter`, `args`, `timeout_seconds`, `env` | [python.md](nodes/python.md) |
| `human` | Pauses in the Web UI awaiting human input.                                                                                                                                   | `description` | [human.md](nodes/human.md) |
| `subgraph` | Embeds a child DAG to reuse complex flows.                                                                                                                                   | source `type` and `config` | [subgraph.md](nodes/subgraph.md) |
| `passthrough` | Pass-through node that forwards only the last message by default and can be configured to forward all messages; used for context filtering and graph structure optimization. | `only_last_message` | [passthrough.md](nodes/passthrough.md) |
| `literal` | Emits a fixed text payload whenever triggered and discards inputs.                                                                                                           | `content`, `role` (`user`/`assistant`) | [literal.md](nodes/literal.md) |
| `loop_counter` | Guard node that limits loop iterations before releasing downstream edges.                                                                                                    | `max_iterations`, `reset_on_emit`, `message` | [loop_counter.md](nodes/loop_counter.md) |
| `loop_timer` | Guard node that limits loop duration before releasing downstream edges.                                                                                                      | `max_duration`, `duration_unit`, `reset_on_emit`, `message`, `passthrough` | [loop_timer.md](nodes/loop_timer.md) |

Fetch the full schema via `POST /api/config/schema` or inspect the dataclasses inside `entity/configs/`.

## 4. Providers & Agent Settings
- Set `provider` and model `name` in each `agent` node's `config`; both are required by `AgentConfig`.
- Fields such as `name`, `api_key`, and `base_url` accept `${VAR}` placeholders for environment portability.
- Different Agent nodes may specify different providers; retry options belong to each Agent's `config.retry`.

### 4.1 Gemini Provider Config Example
Inside an `agent` node's `config`:
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
The Gemini Provider supports multi-modal input (images/video/audio are automatically converted to Parts) and supports `function_calling_config` to control tool execution behavior.

## 5. Edges & Conditions
- Basic edge:
  ```yaml
  - source: plan
    target: execute
  ```
- Conditional edge:
  ```yaml
  edges:
    - source: router
      target: analyze
      condition: should_analyze   # functions/edge/should_analyze.py
  ```
- If a `condition` function raises, the scheduler marks the branch as failed and stops downstream execution.

### 5.1 Edge Payload Processors
- Add `process` to an edge when you want to transform or filter the payload after the condition is met (e.g., extract a verdict, keep only structured fields, or rewrite text).
- Structure mirrors `condition` (`type + config`). Built-ins include:
  - `regex_extract`: Python regex with optional `group`, `mode` (`replace_content`, `metadata`, `data_block`), `multiple`, and `on_no_match` (`pass`, `default`, `drop`).
  - `function`: calls helpers under `functions/edge_processor/`. The handler signature is `def foo(payload: Message, **kwargs) -> Message | None`. Note: The Processor interface is now standardized, and `kwargs` includes `context: ExecutionContext`, allowing access to the current execution context.
- Example:
  ```yaml
  - from: reviewer
    to: qa
    process:
      type: regex_extract
      config:
        pattern: "Score\\s*:\\s*(?P<score>\\d+)"
        group: score
        mode: metadata
        metadata_key: quality_score
        case_sensitive: false
        on_no_match: default
        default_value: "0"
  ```

## 6. Agent Node Advanced Features
- **Tooling**: Configure `AgentConfig.tooling`; see the [Tooling module](modules/tooling/README.md) (Chinese for now).
- **Thinking**: Enable staged reasoning via `AgentConfig.thinking` (e.g., chain-of-thought, reflection). Reference `entity/configs/node/thinking.py` for parameters.
- **Memories**: Attach `MemoryAttachmentConfig` through `AgentConfig.memories`; details live in the [Memory module](modules/memory.md).

## 7. Dynamic Execution (Map-Reduce/Tree)
`dynamic` belongs to an **edge** and expands its target node. See the [Dynamic Execution guide](dynamic_execution.md) for the Map/Tree config, split strategies, and a checked-in edge example.

## 8. Design Template Export
After editing configs or `FIELD_SPECS`, regenerate templates:
```bash
python -m tools.export_design_template \
  --output yaml_template/design.yaml \
  --mirror frontend/public/design_0.4.0.yaml
```
- The script scans registered nodes, memories, tooling, and `FIELD_SPECS` to emit YAML templates plus the frontend mirror file.
- Commit the generated files and notify frontend owners to refresh static assets.

## 9. CLI / API Execution Paths
- **Web UI**: Choose a YAML file, fill run parameters, start execution, and monitor in the dashboard. *Recommended path.*
- **HTTP**: Establish a WebSocket `/ws` session first, then `POST /api/workflow/execute` with `yaml_file`, `task_prompt`, its current `session_id`, optional `attachments`, and `log_level` (`INFO` or `DEBUG`).
- **CLI**: `python run.py --path yaml_instance/net_example.yaml --name test_run`. Respond to the CLI prompt for the task text.

## 10. Debugging Tips
- Use the Web UI context snapshots or `WareHouse/<session>/context.json` to inspect node I/O. Note that all node outputs are now standardized as `List[Message]`.
- Leverage the Schema API breadcrumbs ([config_schema_contract.md](config_schema_contract.md)) or run `python run.py --inspect-schema` to view field specs quickly.
- Missing YAML placeholders trigger `ConfigError` during parsing with a precise path surfaced in both UI and CLI logs.
