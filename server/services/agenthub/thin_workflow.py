"""Validate allowlisted workflows as static, single-Agent execution units."""

from dataclasses import dataclass
from pathlib import Path

import yaml

# runtime.sdk initializes the runtime package before importing check.check.
from runtime.sdk import load_config
from check.check import DesignError
from entity.configs import ConfigError
from entity.configs.graph import GraphDefinition

from .metadata import AgentMetadataInput
from .runtime_resolver import RuntimeRefResolver


class WorkflowValidationError(ValueError):
    """A safe error that does not expose workflow paths or configuration."""

    def __init__(self) -> None:
        self.code = "INVALID_WORKFLOW"
        super().__init__(self.code)


@dataclass(frozen=True)
class ValidatedWorkflow:
    runtime_ref: str
    revision: int
    graph_id: str
    agent_node_id: str
    path: Path


class ThinWorkflowValidator:
    def __init__(self, resolver: RuntimeRefResolver) -> None:
        self.resolver = resolver

    def validate(self, runtime_ref: str) -> ValidatedWorkflow:
        path = self.resolver.resolve(runtime_ref)
        try:
            design = load_config(path)
        except (ConfigError, DesignError, yaml.YAMLError, OSError, UnicodeError):
            raise WorkflowValidationError() from None

        agent_node_id = self._single_agent_path(design.graph)
        reference = AgentMetadataInput.validate_runtime_ref(runtime_ref)
        return ValidatedWorkflow(
            runtime_ref=reference,
            revision=int(reference.rsplit("/", 1)[1]),
            graph_id=design.graph.id,
            agent_node_id=agent_node_id,
            path=path,
        )

    @staticmethod
    def _single_agent_path(graph: GraphDefinition) -> str:
        if graph.is_majority_voting or not graph.id:
            raise WorkflowValidationError()
        if len(graph.start_nodes) != 1 or not graph.end_nodes or len(graph.end_nodes) != 1:
            raise WorkflowValidationError()

        nodes = {node.id: node for node in graph.nodes}
        agents = [node.id for node in graph.nodes if node.type == "agent"]
        if len(agents) != 1 or any(
            node.type not in {"agent", "passthrough"} or node.input or node.output
            for node in graph.nodes
        ):
            raise WorkflowValidationError()

        next_node: dict[str, str] = {}
        for edge in graph.edges:
            if (
                not edge.trigger
                or not edge.carry_data
                or edge.keep_message
                or edge.clear_context
                or edge.clear_kept_context
                or edge.process is not None
                or edge.dynamic is not None
                or edge.condition is None
                or edge.condition.to_external_value() != "true"
                or edge.source in next_node
            ):
                raise WorkflowValidationError()
            next_node[edge.source] = edge.target

        current = graph.start_nodes[0]
        visited: set[str] = set()
        while current in nodes:
            if current in visited:
                raise WorkflowValidationError()
            visited.add(current)
            if current not in next_node:
                break
            current = next_node[current]
        if current != graph.end_nodes[0] or len(visited) != len(nodes):
            raise WorkflowValidationError()
        return agents[0]
