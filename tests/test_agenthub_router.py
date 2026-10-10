"""Semantic routing decisions without workflow or provider execution."""

from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import UUID

import pytest

from server.services.agenthub.discovery import (
    AgentDiscovery, DiscoveryCandidate, PublicAgentMetadata, discovery_text, prepare_embeddings,
)
from server.services.agenthub.embeddings import EmbeddingValidationError, FakeEmbeddingBackend
from server.services.agenthub.metadata import AgentMetadata, AgentMetadataInput, AgentMetadataVersion
from server.services.agenthub.router import CalibratedThreshold, SemanticRouter
from server.services.agenthub.runtime_resolver import RuntimeReferenceError


TASK = "route this task"


def _candidate(number: int, score: float) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        agent_id=UUID(int=number),
        version=2,
        public_metadata=PublicAgentMetadata(
            name=f"Agent {number}",
            description="Public capability",
            capabilities=("summarize reports",),
            tags=("internal",),
            tools=(),
        ),
        raw_similarity=score,
    )


class ScriptedDiscovery:
    def __init__(self, candidates=(), error=None):
        self.candidates = tuple(candidates)
        self.error = error
        self.calls = []

    def discover(self, task, *, k):
        self.calls.append((task, k))
        if self.error is not None:
            raise self.error
        return self.candidates[:k]


def _router(discovery, *, value=0.5, top_k=3):
    return SemanticRouter(
        discovery,
        threshold=CalibratedThreshold(value=value, source="fixture-calibration-v1"),
        top_k=top_k,
    )


def test_no_candidates_rejects_without_selection_or_execution():
    discovery = ScriptedDiscovery()
    with patch("workflow.graph.GraphExecutor.run") as workflow_run, patch(
        "runtime.node.agent.providers.base.ProviderRegistry.get_provider"
    ) as provider_lookup:
        result = _router(discovery).route(TASK)

    assert result.status == "rejected"
    assert result.strategy == "semantic"
    assert result.reason_code == "NO_SUITABLE_AGENT"
    assert result.selected_agent is None
    assert result.candidates == ()
    assert result.error_code is None
    assert result.threshold.source == "fixture-calibration-v1"
    assert discovery.calls == [(TASK, 3)]
    workflow_run.assert_not_called()
    provider_lookup.assert_not_called()


@pytest.mark.parametrize(
    "score,expected_status",
    [(0.49, "rejected"), (0.5, "selected"), (0.51, "selected")],
)
def test_threshold_compares_raw_cosine_and_preserves_candidate(score, expected_status):
    candidate = _candidate(1, score)
    result = _router(ScriptedDiscovery((candidate,)), value=0.5).route(TASK)

    assert result.status == expected_status
    assert result.candidates == (candidate,)
    assert result.candidates[0].raw_similarity == score
    assert result.threshold.value == 0.5
    assert result.selected_agent == (candidate if expected_status == "selected" else None)
    assert result.reason_code == (
        None if expected_status == "selected" else "NO_SUITABLE_AGENT"
    )
    assert result.error_code is None
    assert not hasattr(result, "confidence")


def test_selects_stable_first_candidate_and_retains_ordered_top_k():
    first, second, third = _candidate(1, 0.75), _candidate(2, 0.75), _candidate(3, 0.25)
    discovery = ScriptedDiscovery((first, second, third))
    with patch("workflow.graph.GraphExecutor.run") as workflow_run, patch(
        "runtime.node.agent.providers.base.ProviderRegistry.get_provider"
    ) as provider_lookup:
        result = _router(discovery, top_k=2).route(TASK)

    assert result.status == "selected"
    assert result.selected_agent == first
    assert result.candidates == (first, second)
    assert discovery.calls == [(TASK, 2)]
    workflow_run.assert_not_called()
    provider_lookup.assert_not_called()


@pytest.mark.parametrize(
    "error,expected_code",
    [
        (EmbeddingValidationError("ZERO_VECTOR"), "ZERO_VECTOR"),
        (RuntimeReferenceError("INVALID_WORKFLOW_MANIFEST"), "INVALID_WORKFLOW_MANIFEST"),
        (KeyError("secret provider token"), "ROUTING_INFRASTRUCTURE_ERROR"),
    ],
)
def test_discovery_errors_fail_with_safe_code_and_no_selection(error, expected_code):
    result = _router(ScriptedDiscovery(error=error)).route(TASK)

    assert result.status == "failed"
    assert result.error_code == expected_code
    assert result.candidates == ()
    assert result.selected_agent is None
    assert result.reason_code is None
    assert "secret provider token" not in repr(result)


def test_real_query_embedding_failure_is_infrastructure_failure_without_execution():
    content = AgentMetadataInput(
        name="Research", description="Finds information",
        capabilities=("search technical information",),
        runtime_ref="workflow://research/1",
    )
    snapshot = AgentMetadataVersion(**content.model_dump(), id=UUID(int=1), version=1)
    now = datetime.now(timezone.utc)
    agent = AgentMetadata(snapshot=snapshot, status="active", created_at=now, updated_at=now)
    backend = FakeEmbeddingBackend(
        {discovery_text(snapshot): (1.0, 0.0)},
        model_key="fixture", dimensions=2,
    )
    versions = Mock()
    versions.list_current.return_value = (agent,)
    index = Mock()
    index.get.return_value = prepare_embeddings((snapshot,), backend)[0]
    discovery = AgentDiscovery(versions, index, Mock(), backend)

    with patch("workflow.graph.GraphExecutor.run") as workflow_run, patch(
        "runtime.node.agent.providers.base.ProviderRegistry.get_provider"
    ) as provider_lookup:
        result = _router(discovery).route(TASK)

    assert result.status == "failed"
    assert result.error_code == "ROUTING_INFRASTRUCTURE_ERROR"
    assert result.selected_agent is None
    assert result.reason_code is None
    assert "not configured" not in repr(result)
    workflow_run.assert_not_called()
    provider_lookup.assert_not_called()


@pytest.mark.parametrize(
    "value,source",
    [(-1.1, "benchmark"), (1.1, "benchmark"), (float("nan"), "benchmark"),
     (float("inf"), "benchmark"), (True, "benchmark"), (0.5, " ")],
)
def test_threshold_requires_bounded_cosine_and_calibration_source(value, source):
    with pytest.raises(ValueError):
        CalibratedThreshold(value=value, source=source)


@pytest.mark.parametrize("top_k", [0, -1, True, 1.5])
def test_top_k_must_be_positive_integer(top_k):
    with pytest.raises(ValueError, match="top_k"):
        _router(ScriptedDiscovery(), top_k=top_k)
