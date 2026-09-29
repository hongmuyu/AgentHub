# AgentHub Development Instructions

## 1. Project Background

This repository is being used as the foundation for a resume-level secondary development project named **AgentHub**.

The upstream foundation is:

- OpenBMB / ChatDev 2.0 / DevAll

AgentHub is NOT intended to replace or rewrite ChatDev.

The goal is to reuse the existing:

- Multi-Agent Runtime
- Workflow system
- FastAPI backend
- Frontend
- Tool system

and extend them with Agent Platform capabilities.

The project should demonstrate practical **AI Agent Platform / Multi-Agent infrastructure engineering** skills.

---

## 2. Business Scenario

AgentHub is positioned as an:

> Enterprise internal AI Agent registration, capability discovery, dynamic routing, execution, and observability platform.

Two conceptual user perspectives guide the P0 experience:

- Platform administrator: register/manage Agents, inspect capabilities/version/status, enable/disable Agents, and observe platform behavior through a minimal Registry UI.
- Business/end user: submit a natural-language task, inspect discovered candidates and the selected Agent, execute through ChatDev Runtime, and inspect the result, business state, and routing trace.

P0 assumes trusted local/internal usage. These perspectives do not introduce multi-tenancy, complex RBAC, approval workflows, or a Marketplace.

As the number of Agents inside an organization increases, users should not need to know the exact name of every Agent.

Users submit a task in natural language.

The platform should:

1. understand what capability the task requires;
2. discover suitable Agents from an Agent Registry;
3. rank candidate Agents;
4. select the most appropriate Agent;
5. execute the Agent through the existing ChatDev Runtime / Workflow;
6. record execution state and trace information;
7. expose runtime metrics and evaluation results.

The central problem is:

> How can multiple Agents be registered, discovered, selected, executed, and observed?

---

## 3. Portfolio Positioning

This is the second Agent portfolio project.

### Project 1: TaskPilot

TaskPilot focuses on the **Agent application layer**, including:

- intent recognition;
- multi-turn memory;
- MCP / Tool usage;
- tool parameter handling;
- retry / fallback;
- Agent reliability.

### Project 2: AgentHub

AgentHub focuses on the **Agent platform layer**, including:

- Agent Registry;
- Agent Discovery;
- Capability Routing;
- Workflow integration;
- Run State;
- Observability;
- Evaluation.

Do NOT duplicate TaskPilot functionality unless required by the existing ChatDev architecture.

---

## 4. Core AgentHub Architecture

Target conceptual flow:

```text
User Task
    ↓
Capability Query
    ↓
Agent Registry
    ↓
Agent Discovery
    ↓
Candidate Agents
    ↓
Semantic Routing
    ↓
Optional LLM Rerank
    ↓
Selected Agent
    ↓
Existing ChatDev Workflow / Runtime
    ↓
Execution
    ↓
Run State / Trace / Metrics
```

The Router should depend on Agent capability metadata instead of hardcoded Agent names.

Adding a new Agent should ideally require registering its metadata rather than modifying multiple router branches.

Avoid designs such as:

```python
if task_type == "code":
    return CodeAgent

if task_type == "research":
    return ResearchAgent
```

Prefer capability-driven discovery and routing.

AgentHub is a general-purpose Agent platform. Discovery and Routing operate on registered Agent metadata and capabilities; adding a compatible Agent should not normally require Router source changes.

---

## 5. MVP Scope

The approximately 15-day resume-level MVP should focus on:

### P0

1. Agent Registry
2. Capability Metadata
3. Agent Discovery
4. Semantic Routing
5. Workflow Integration
6. Run State
7. Routing / Execution Trace
8. Basic Runtime Metrics
9. Basic Evaluation
10. Automated Tests
11. README / Architecture documentation
12. Local Docker-based reproducible execution

---

## 6. Agent Registry

Expected conceptual metadata:

```text
AgentMetadata
├── id
├── name
├── description
├── capabilities
├── tools
├── tags
├── version
├── status
└── runtime_ref
```

Example:

```json
{
  "id": "research-agent",
  "name": "Research Agent",
  "description": "Searches and summarizes technical information",
  "capabilities": [
    "search technical information",
    "summarize research findings",
    "compare technologies"
  ],
  "tools": [
    "web_search"
  ],
  "tags": [
    "research",
    "knowledge"
  ],
  "version": "1.0.0",
  "status": "active"
}
```

Expected MVP lifecycle operations:

- Register
- Update
- Get
- List
- Search
- Enable
- Disable

Do NOT build complex:

- RBAC;
- approval workflows;
- multi-tenancy;
- Agent Marketplace;
- gray releases.

---

## 7. Demo Agents

The current conceptual demo set contains approximately six Agents:

### ResearchAgent

Capabilities:

- search technical information;
- gather external knowledge;
- compare technologies;
- summarize research findings.

### CodeAgent

Capabilities:

- analyze source code;
- generate code;
- debug code;
- explain implementations.

### DataAgent

Capabilities:

- analyze structured data;
- calculate statistics;
- extract trends;
- generate data insights.

### DocumentAgent

Capabilities:

- summarize documents;
- extract information;
- organize long-form content;
- compare documents.

### PlanningAgent

Capabilities:

- decompose complex tasks;
- create execution plans;
- prioritize subtasks;
- define milestones.

### ReviewAgent

Capabilities:

- review generated output;
- identify errors;
- evaluate quality;
- suggest improvements.

These Agents form an enterprise R&D / knowledge-work catalog of ordinary metadata/workflow fixtures, not hardcoded supported types or six completed platform Agents. Each fixture needs a business-facing name/description, capabilities and boundaries, tags, tools when appropriate, a controlled runtime_ref, and a thin workflow. Intentional capability overlap supports meaningful ambiguous routing cases. Adding a seventh compatible Agent must not require Router source changes.

Do NOT implement them blindly if ChatDev already has reusable Agents or roles.

First inspect the existing architecture and determine how these concepts should map to ChatDev.

---

## 8. Routing

The target MVP routing pipeline is approximately:

```text
Task
 ↓
Rule / Status Filter
 ↓
Semantic Matching
 ↓
Top-K Agents
 ↓
Optional LLM Rerank
 ↓
Selected Agent
```

Two routing strategies should eventually be comparable:

### Strategy A

```text
Semantic Retrieval
→ Top-1 Agent
```

### Strategy B

```text
Semantic Retrieval
→ Top-K
→ LLM Rerank
→ Selected Agent
```

LLM should NOT necessarily receive every registered Agent and perform the entire routing process from scratch.

Prefer semantic retrieval for candidate recall and use LLM reranking only when useful.

Support a confidence threshold where practical:

```text
low routing confidence
→ NO_SUITABLE_AGENT
```

Do not force every input into an unrelated Agent.

---

## 9. MVP Routing Boundary

The MVP routes one task to **one selected Agent**.

Target:

```text
Task
 ↓
Router
 ↓
Selected Agent
 ↓
ChatDev Workflow
```

Dynamic Agent Team construction is NOT part of P0.

Possible P1:

```text
Task
 ↓
Top-K Agents
 ↓
Dynamic Agent Team
```

Do not introduce Agent-team coordination complexity during the initial MVP unless the existing ChatDev architecture makes it nearly free.

---

## 10. State Management

The project does NOT need a large custom state machine.

Conceptual AgentHub business Run states:

- pending
- running
- success
- failed
- rejected
- cancelled

Keep this business status separate from native ChatDev execution/session status, preserving the native status where useful.

Target conceptual runtime data:

```text
TaskRun
RoutingTrace
AgentRun
```

Possible RoutingTrace fields:

```text
task
candidate_agents
candidate_scores
routing_strategy
selected_agent
routing_latency
```

Possible AgentRun fields:

```text
agent_id
workflow_id
status
latency
token_usage
error
result
```

---

## 11. Failure Handling

P0 failure handling should remain simple.

Examples:

### No suitable Agent

```text
Task
 ↓
Router
 ↓
confidence below threshold
 ↓
NO_SUITABLE_AGENT
```

### Agent execution failure

```text
Selected Agent
 ↓
Execution Failure
 ↓
Run.status = failed
 ↓
Error Trace
```

Do NOT build complicated automatic fallback, retry chains, or secondary-Agent switching in P0.

---

## 12. Observability

The project should provide lightweight runtime observability.

Useful metrics include:

- Total Runs
- Success Rate
- Failure Rate
- Average Latency
- Routing Latency
- Token Usage
- Agent Usage
- Routing Distribution

Prefer a lightweight implementation that integrates naturally with the existing project.

Do NOT introduce a large observability stack such as Kubernetes + Prometheus + Grafana purely for the resume project.

---

## 13. Evaluation

Evaluation is a first-class project feature.

Target approximately 60 routing benchmark cases initially, following the frozen design distribution:

```text
36 clear cases
+ 12 ambiguous / cross-capability cases
+ 12 no-match cases
```

Potential metrics:

- Top-1 Routing Accuracy
- Top-K Recall
- Routing Latency
- Reject Accuracy / False Accept Rate
- Execution Success Rate
- Task Quality Success Rate (only with assertions, rubric, or human evaluation)

If both routing strategies are implemented, compare:

```text
Semantic Only

vs

Semantic + LLM Rerank
```

Results must come from actual benchmark execution.

Do not fabricate benchmark numbers.

---

## 14. Delivery Requirements

The MVP should ultimately provide:

- GitHub repository
- clear README
- architecture diagram
- local reproducible startup
- automated tests
- routing benchmark results
- 3–5 minute Demo video

Public cloud deployment is optional P1.

The MVP must not be blocked by public deployment.

Local Docker-based reproducible execution is more important.

---

## 15. Non-Goals

Do NOT expand the MVP into:

- a new Multi-Agent Framework;
- complex A2A infrastructure;
- distributed Runtime;
- distributed Scheduler;
- Kubernetes;
- automatic scaling;
- complex RBAC;
- multi-tenancy;
- Agent Marketplace;
- complex conversation memory;
- MCP Tool Router;
- complex tool retry / fallback;
- autonomous dynamic DAG generation;
- complex dynamic Agent teams.

These are outside the current project scope.

---

## 16. Engineering Principles

Follow these rules throughout development.

### Understand Before Editing

Before implementing a feature:

1. inspect the relevant existing code;
2. identify reusable components;
3. explain the current execution path;
4. identify the smallest insertion point.

Never assume the architecture.

---

### Prefer Reuse

Prefer:

```text
Reuse existing component
→ Extend minimally
→ Add new module only when necessary
```

Avoid replacing existing ChatDev infrastructure unless there is a concrete technical reason.

---

### Minimum Necessary Changes

Do not perform unrelated refactoring.

Do not clean up adjacent code unless required by the current task.

Avoid large architectural rewrites.

---

### Feature-by-Feature Development

Implement one feature at a time.

Recommended general sequence:

```text
Architecture Recon
↓
Agent Registry
↓
Discovery
↓
Semantic Routing
↓
Workflow Integration
↓
Run State / Trace
↓
Observability
↓
Evaluation
↓
Tests
↓
Documentation / Demo
```

Each feature must be independently understandable and testable.

---

### Tests Are Required

A feature is not complete simply because the happy-path demo works.

Add appropriate:

- unit tests;
- API tests;
- routing tests;
- integration tests

depending on the feature.

---

### Evidence-Based Decisions

Whenever making claims about the current ChatDev architecture:

- inspect the actual implementation;
- provide file paths;
- identify relevant classes and functions.

Do not invent architecture based on naming assumptions.

---

## 17. Current Development Phase

The project is currently in:

> Architecture frozen — final pre-implementation planning/workflow adjustment

Architecture Recon is complete in `ARCHITECTURE_RECON.md`; `AGENTHUB_DESIGN.md` has status `Design Frozen for P0 Implementation` and is the authority for technical decisions. `TASKS.md` contains the approved 50 P0 tasks, T00–T49. Do not redesign AgentHub or reinterpret early conceptual examples as overriding frozen contracts.

The current documentation adjustment authorizes only AGENTS.md and TASKS.md changes. Do not execute T00, start functional tasks, or modify production source during this adjustment. Later implementation sessions select one task explicitly and stop after its completion report.

Keep frozen architecture choices in the design artifact rather than duplicating a competing specification here. T49 is the explicitly approved lightweight Registry Admin UI scope addition; a full management console remains deferred. Exact API/module placement remains an implementation detail to verify against source, and benchmark results require actual measurements.

---

## 18. Current Guiding Principle

The main objective is not:

> Make ChatDev contain as many new features as possible.

The objective is:

> Build a focused, explainable, testable Agent Platform extension within approximately 15 days, while demonstrating strong understanding of Agent Registry, capability discovery, dynamic routing, runtime state, observability, evaluation, and secondary development of an existing codebase.

---

## Verified ChatDev Architecture Facts

Architecture Recon verified these facts against OpenBMB/ChatDev commit `4fb2db0ea90375ce1059f44fe03ffbd191a7a169`:

- A runtime Agent is `Node(type="agent") + AgentConfig`. Business Agent identity and metadata must remain separate from model name, provider, node type, and role / system prompt; provider/client object identity is not a business Agent ID.
- Existing `node_registry`, `ProviderRegistry`, and `schema_registry` register execution types, providers, and configuration schemas; none is an AgentHub business Agent registry.
- Existing graph execution is YAML-defined and topology-driven. Reuse its Workflow / Runtime orchestration rather than replacing it.
- Business discovery and routing belong at the application-service boundary, before existing Workflow / Runtime execution; do not put global Agent discovery in `DAGExecutor`, `AgentNodeExecutor`, or node-type dispatch.
- Web session state is primarily in memory and does not provide persistent AgentHub Run storage.
- Reuse `WorkflowRunService`, Workflow / Runtime, tools, WebSocket execution, attachments, memory, `WorkflowLogger`, `LogManager`, and `TokenTracker` where appropriate.
- Existing workflow completion does not reliably imply business-task success. AgentHub remains an application/platform extension around ChatDev Runtime, not a replacement Multi-Agent framework.

## runtime_ref Contract

For P0, prefer this conceptual execution contract:

```text
AgentMetadata
    ↓
runtime_ref
    ↓
validated thin workflow representing one business Agent
    ↓
WorkflowRunService
    ↓
existing ChatDev Runtime
```

The `runtime_ref` must resolve through a controlled mapping, not expose arbitrary user-controlled filesystem paths. An arbitrary node inside a complex workflow is not the default P0 execution unit. Do not dynamically generate DAGs for normal P0 routing or create one executor class per demo Agent. Continue to reuse existing `type: agent`, `AgentConfig`, providers, tools, memory, thinking, and Runtime. Follow the frozen logical-reference/allowlist contract in AGENTHUB_DESIGN.md.

## Execution Success Semantics

```text
workflow_completed != execution_success
Execution Success Rate != Task Quality Success Rate
```

AgentHub business Run status distinguishes `pending`, `running`, `success`, `failed`, `rejected`, and `cancelled`. `NO_SUITABLE_AGENT` is a routing rejection, not successful execution. Execution success requires the selected Agent to have actually succeeded under the structured outcome contract, normal workflow completion, and no failure/cancellation signal; `completed` alone must not map to `success`. Task quality success requires assertions, rubric, or human evaluation; without evaluation it is unmeasured. Preserve native ChatDev execution/session status separately, and keep rejection, failure, and cancellation distinct in metrics/evaluation. Follow the frozen state and persistence semantics in AGENTHUB_DESIGN.md.

## Permanent Per-Task Git Workflow

Development branch: `agenthub-dev`. Expected `origin`: `git@github.com:hongmuyu/AgentHub.git`. Expected `upstream`: OpenBMB/ChatDev (currently `https://github.com/OpenBMB/ChatDev.git`). Verify branch/remotes before publishing; do not silently overwrite an unexpected remote.

Normal lifecycle:

```text
read selected task → inspect relevant existing source → implement only that task
→ task-specific tests → relevant regression tests → full git diff review
→ focused commit → push origin/agenthub-dev → completion report → stop
```

Every completed code-changing task normally requires a focused commit and successful `git push origin agenthub-dev`. Stage only authorized files; never include credentials, `.env`, temporary DBs, generated junk, or unrelated changes. Report implementation, changed files, task tests, regressions, PASS/FAIL/BLOCKED/NOT VERIFIED items, commit SHA, push result, remote branch, final git status, and COMPLETE/BLOCKED. Do not automatically start the next task, force push, rewrite published history, or merge each individual task into main.

T00 is a verification exception: evidence is mandatory, but commit/push is required only if it actually changes an authorized tracked document. With no repository changes: verify → report results/current HEAD/git status → no commit required. Never create an empty/no-op commit to satisfy a checklist.

## Automatic Milestone Merge Policy

Automatic `agenthub-dev → main` merge is authorized only after a complete M1, M2, M3, or M4 gate passes. TASKS.md defines cumulative milestone membership: M1 Registry + Semantic Routing; M2 End-to-End Agent Execution; M3 Observable Business Demo (including T49 administrator experience); M4 Evaluation + Delivery. This documentation-only adjustment is not a milestone completion and must not be merged merely because it is committed.

Before merging, verify all ten gates with recorded evidence:

1. Every task required by the milestone is COMPLETE.
2. Task-specific tests pass.
3. Milestone integration tests pass.
4. Relevant legacy ChatDev regressions pass, with inherited issues classified separately as below.
5. No new unresolved P0 blocker exists.
6. Inherited known issues are clearly distinguished from new regressions.
7. The working tree is clean.
8. No secret, `.env`, temporary DB, generated junk, or unrelated file is staged or included in the changes to merge.
9. `agenthub-dev` has been pushed successfully and matches `origin/agenthub-dev`.
10. The expected main history is present and the merge can finish without unresolved conflict.

An inherited known issue (such as the documented WebSocket fixture blocker) does not automatically block a milestone if evidence shows it existed before AgentHub and the milestone introduced no regression in that area. Report the inherited failure honestly; do not call it an AgentHub regression or claim the full suite passed. Missing required evidence still blocks merging; explicitly optional live-provider checks may remain NOT VERIFIED under their task acceptance rules.

When the gate passes, first run:

```bash
git branch --show-current
git status
git remote -v
git fetch origin
```

Ensure the expected remotes, local `agenthub-dev`/`main`, and remote `origin/agenthub-dev`/`origin/main` exist. Inspect ancestry/divergence and confirm main has no unexpected local commits; do not invent or rewrite branch history. Missing `origin/main`, unexpected branches/remotes/history, or unresolved conflict means BLOCKED. Then use the applicable milestone message:

```bash
git checkout main
git pull --ff-only origin main
git merge --no-ff agenthub-dev -m "merge: complete AgentHub M1 registry and routing"
git push origin main
git checkout agenthub-dev
```

For M2/M3/M4, replace only the merge message with:

- `merge: complete AgentHub M2 runtime integration`
- `merge: complete AgentHub M3 observable business demo`
- `merge: complete AgentHub M4 P0 delivery`

Check each command's exit status before continuing. If a merge conflicts, do not publish it or resolve it speculatively; abort that merge, return to agenthub-dev when safe, and report BLOCKED. If push fails, report the exact unpublished state without rewriting history. Verify the final branch/status and remote merge SHA after success. Do not delete agenthub-dev or force push.

Automatic merge is forbidden for incomplete required tasks, new required test failures, new AgentHub regressions, unmet acceptance criteria, unresolved conflicts, a dirty/unrelated working tree, unexpected remotes/branches/history, missing required evidence, or fabricated benchmark results. In that situation, push safe completed commits to the verified `origin/agenthub-dev` when safe, report the blocker, and DO NOT MERGE. Never publish to an unexpected remote to satisfy the push rule.
