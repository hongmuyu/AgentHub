# Config Schema API Contract

This reference explains how `/api/config/schema` and `/api/config/schema/validate` expose DevAll's dynamic config metadata so IDEs, frontend form builders, and CLI tools can scope schemas with breadcrumbs.

## 1. Endpoints
| Method & Path | Purpose |
| --- | --- |
| `POST /api/config/schema` | Returns the schema for a config node described by breadcrumbs. |
| `POST /api/config/schema/validate` | Validates a YAML/JSON document and optionally echoes the scoped schema. |

### 1.1 Request body (shared fields)
```json
{
  "breadcrumbs": [
    {"node": "DesignConfig", "field": "graph"},
    {"node": "GraphDefinition", "field": "nodes"},
    {"node": "Node", "field": "config", "value": "agent"}
  ]
}
```
- `node` (required): config class name (`DesignConfig`, `GraphDefinition`, `Node`, etc.) that must match the class reached so far.
- `field` (optional): child field to traverse. When omitted, the breadcrumb only asserts you remain on `node`.
- `value` (optional): use when the child class depends on a discriminator (e.g., node `type`). Supply the value as it would appear in YAML.
- `index` (optional int): reserved for list traversal; current configs rely on `value`/`field` for navigation.

### 1.2 `/schema` response
For the request above, `node` is `AgentConfig`. To inspect node types instead, stop after the `GraphDefinition` breadcrumb; the response has `node: "Node"`, fields including `id` and `type`, and `type.enum` values `agent`, `human`, `subgraph`, `python`, `passthrough`, `literal`, `loop_counter`, and `loop_timer`. Field entries use `type`, not `typeHint`. The response also includes `schemaVersion`, `constraints`, `breadcrumbs`, and a computed `cacheKey`.
- `fields`: serialized `ConfigFieldSpec` entries; nested targets include `childRoutes`.
- `constraints`: emitted from `collect_schema()` (mutual exclusivity, required combos, etc.).
- `cacheKey`: SHA-1 hash of `{node, breadcrumbs}` so clients can memoize responses.

### 1.3 `/schema/validate` payloads
Add `document` alongside breadcrumbs:
```json
{
  "breadcrumbs": [{"node": "DesignConfig"}],
  "document": "name: demo\nversion: 0.4.0\nworkflow:\n  nodes: []\n  edges: []\n"
}
```
This document is deliberately invalid: the loader requires a `graph` section.
Responses:
- Valid document: `{ "valid": true, "schema": { ... } }`
- Config error:
  ```json
  {
    "valid": false,
    "error": "root.graph: graph section is required",
    "path": "root.graph"
  }
  ```
  The full response also contains `schema`; its value depends on the requested breadcrumbs.
- Malformed YAML: HTTP 400 with `detail: { "message": "invalid_yaml", "error": "..." }`.

## 2. Breadcrumb Tips
- Begin with `{ "node": "DesignConfig" }`.
- Each hop’s `node` must match the current config class or the API returns HTTP 422.
- Use `field` to step into nested configs (graph → nodes → config, etc.).
- Use `value` for discriminator-based children (node `type`, tooling `type`, etc.).
- Non-navigable targets raise `field '<name>' on <node> is not navigable`.

## 3. CLI Helper
```bash
python run.py --inspect-schema --schema-breadcrumbs '[{"node":"DesignConfig","field":"graph"}]'
```
The CLI prints the same JSON as `/schema`, which is useful while editing `FIELD_SPECS` or debugging registries before exporting templates.

## 4. Frontend Pattern
1. Fetch base schema with `[{node: 'DesignConfig', field: 'graph'}]` to render the workflow form.
2. When users open nested modals (node config, tooling config, etc.), append breadcrumbs describing the path and refetch.
3. Cache responses using `cacheKey` + breadcrumbs.
4. Before saving, call `/schema/validate` to surface `error` + `path` inline.

## 5. Error Reference
| HTTP Code | Situation | Detail payload |
| --- | --- | --- |
| 400 | YAML parse failure | `detail: { "message": "invalid_yaml", "error": "..." }` |
| 422 | Breadcrumb resolution failure | `detail: { "message": "breadcrumb node 'X'..." }` |
| 200 + `valid=false` | Backend `ConfigError` | `{ "error": "...", "path": "root.graph" }` for the example above |
| 200 + `valid=true` | Document OK | Schema echoed back for the requested breadcrumbs. |

Pair this contract with `FIELD_SPECS` to build schema-aware experiences without hardcoding config structures.
