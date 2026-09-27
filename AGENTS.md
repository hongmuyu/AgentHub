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

> Enterprise internal multi-Agent task routing and management platform.

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

These Agents are conceptual requirements.

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

Conceptual states:

- pending
- running
- success
- failed

Prefer reusing existing ChatDev state models if possible.

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

Target approximately 60 routing benchmark cases initially.

Suggested distribution:

```text
6 Agents
×
8 relatively clear tasks
= 48 cases

+

approximately 12 ambiguous / cross-capability tasks
```

Potential metrics:

- Top-1 Routing Accuracy
- Top-K Recall
- Routing Latency
- Task Success Rate

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

> Phase 0 — Architecture Recon

Do NOT implement AgentHub features yet.

Do NOT create a Registry, Router, database schema, vector database, or Dashboard yet.

First understand the existing ChatDev architecture.

The first output should be an architecture investigation report.

Implementation begins only after the architecture report has been reviewed and the AgentHub technical design has been frozen.

---

## 18. Current Guiding Principle

The main objective is not:

> Make ChatDev contain as many new features as possible.

The objective is:

> Build a focused, explainable, testable Agent Platform extension within approximately 15 days, while demonstrating strong understanding of Agent Registry, capability discovery, dynamic routing, runtime state, observability, evaluation, and secondary development of an existing codebase.
