"""Validation of complete, static, single-Agent workflow targets."""

import json
from unittest.mock import patch

import pytest
import yaml

from server.services.agenthub.runtime_resolver import RuntimeRefResolver
from server.services.agenthub.thin_workflow import ThinWorkflowValidator, WorkflowValidationError


REFERENCE = "workflow://research-agent/2"


def _agent(node_id="agent"):
    return {
        "id": node_id,
        "type": "agent",
        "config": {"provider": "openai", "name": "fixture-model", "role": "Summarize the task."},
    }


def _passthrough(node_id):
    return {"id": node_id, "type": "passthrough", "config": {"only_last_message": True}}


def _graph(nodes=None, edges=None, start=None, end=None):
    return {
        "id": "thin_research",
        "start": ["agent"] if start is None else start,
        "end": ["agent"] if end is None else end,
        "nodes": [_agent()] if nodes is None else nodes,
        "edges": [] if edges is None else edges,
    }


def _validator(tmp_path, graph=None, *, yaml_text=None):
    root = tmp_path / "workflows"
    root.mkdir()
    workflow = root / "research.yaml"
    if yaml_text is None:
        yaml_text = yaml.safe_dump({"version": "0.4.0", "vars": {}, "graph": graph or _graph()})
    workflow.write_text(yaml_text, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({REFERENCE: workflow.name}), encoding="utf-8")
    return ThinWorkflowValidator(RuntimeRefResolver(root, manifest)), workflow


def test_single_agent_workflow_records_identity_without_running(tmp_path):
    validator, workflow = _validator(tmp_path)

    with patch(
        "runtime.node.agent.providers.base.ProviderRegistry.get_provider"
    ) as provider_lookup, patch(
        "runtime.node.agent.providers.openai_provider.OpenAIProvider.__init__"
    ) as provider_init, patch("workflow.graph.GraphExecutor.__init__") as runtime_init, patch(
        "workflow.graph.GraphExecutor.run"
    ) as execution:
        result = validator.validate(REFERENCE)

    assert result.runtime_ref == REFERENCE
    assert result.revision == 2
    assert result.graph_id == "thin_research"
    assert result.agent_node_id == "agent"
    assert result.path == workflow.resolve()
    provider_lookup.assert_not_called()
    provider_init.assert_not_called()
    runtime_init.assert_not_called()
    execution.assert_not_called()


def test_passthrough_nodes_can_preserve_task_input_and_agent_output(tmp_path):
    graph = _graph(
        nodes=[_passthrough("input"), _agent(), _passthrough("output")],
        edges=[{"from": "input", "to": "agent"}, {"from": "agent", "to": "output"}],
        start=["input"],
        end=["output"],
    )
    validator, _ = _validator(tmp_path, graph)

    assert validator.validate(REFERENCE).agent_node_id == "agent"


@pytest.mark.parametrize(
    "graph",
    [
        _graph(nodes=[_passthrough("agent")]),
        _graph(nodes=[_agent(), _agent("second")], edges=[{"from": "agent", "to": "second"}],
               end=["second"]),
        _graph(start=[]),
        _graph(end=[]),
        _graph(end=["missing"]),
        _graph(nodes=[_agent(), _passthrough("output")],
               edges=[{"from": "agent", "to": "output"}],
               start=["agent", "output"], end=["output"]),
        _graph(nodes=[_agent(), _passthrough("output")],
               edges=[{"from": "agent", "to": "output"}],
               end=["agent", "output"]),
        _graph(nodes=[{**_agent(), "input": ["preloaded"]}]),
        _graph(nodes=[_agent(), _passthrough("orphan")]),
        _graph(nodes=[_agent(), _passthrough("output")],
               edges=[{"from": "agent", "to": "output"}]),
        _graph(nodes=[_agent(), _passthrough("output")],
               edges=[{"from": "agent", "to": "output"},
                      {"from": "output", "to": "agent"}]),
        _graph(nodes=[_agent(), {"id": "team", "type": "subgraph",
                                "config": {"type": "file", "config": {"path": "team.yaml"}}}]),
        _graph(nodes=[_agent(), {"id": "fixed", "type": "literal",
                                "config": {"content": "fixed", "role": "user"}}]),
        {**_graph(), "is_majority_voting": True},
    ],
)
def test_invalid_or_non_thin_graph_is_rejected(tmp_path, graph):
    validator, workflow = _validator(tmp_path, graph)

    with pytest.raises(WorkflowValidationError) as error:
        validator.validate(REFERENCE)
    assert error.value.code == "INVALID_WORKFLOW"
    assert str(workflow) not in str(error.value)


@pytest.mark.parametrize(
    "edge",
    [
        {"from": "agent", "to": "output", "trigger": False},
        {"from": "agent", "to": "output", "carry_data": False},
        {"from": "agent", "to": "output", "condition": False},
        {"from": "agent", "to": "output", "dynamic": {"type": "map", "split": None,
                                                  "config": {"max_parallel": 5}}},
    ],
)
def test_non_static_or_non_carrying_edge_is_rejected(tmp_path, edge):
    graph = _graph(
        nodes=[_agent(), _passthrough("output")], edges=[edge], end=["output"]
    )
    validator, _ = _validator(tmp_path, graph)
    with pytest.raises(WorkflowValidationError) as error:
        validator.validate(REFERENCE)
    assert error.value.code == "INVALID_WORKFLOW"


def test_invalid_yaml_is_rejected_without_path_echo(tmp_path):
    validator, workflow = _validator(tmp_path, yaml_text="graph: [")
    with pytest.raises(WorkflowValidationError) as error:
        validator.validate(REFERENCE)
    assert error.value.code == "INVALID_WORKFLOW"
    assert str(workflow) not in str(error.value)


def test_placeholder_resolution_error_is_safe(tmp_path):
    data = {"version": "0.4.0", "vars": {"A": "${B}", "B": "${A}"}, "graph": _graph()}
    validator, workflow = _validator(tmp_path, yaml_text=yaml.safe_dump(data))

    with pytest.raises(WorkflowValidationError) as error:
        validator.validate(REFERENCE)
    assert error.value.code == "INVALID_WORKFLOW"
    assert str(workflow) not in str(error.value)
