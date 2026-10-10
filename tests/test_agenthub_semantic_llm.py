"""Shared semantic gate followed by candidate-only reranking."""

import json
from unittest.mock import patch
from uuid import UUID

import pytest

from server.services.agenthub.discovery import DiscoveryCandidate, PublicAgentMetadata
from server.services.agenthub.embeddings import EmbeddingValidationError
from server.services.agenthub.reranker import RerankAdapter, RerankResult
from server.services.agenthub.router import (
    CalibratedThreshold, SemanticLLMRouter, SemanticRouter,
)


TASK = "Choose an agent"
FIRST_ID, SECOND_ID = UUID(int=1), UUID(int=2)


def _candidate(agent_id, score):
    return DiscoveryCandidate(
        agent_id=agent_id,
        version=2,
        public_metadata=PublicAgentMetadata(
            name=f"Agent {agent_id.int}", description="Public capability",
            capabilities=("analyze reports",), tags=("internal",), tools=(),
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


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def complete(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def _response(*agent_ids):
    return json.dumps({
        "candidate_ids": [str(agent_id) for agent_id in agent_ids],
        "reason_code": "CAPABILITY_MATCH",
    })


def _semantic(discovery, threshold=0.5):
    return SemanticRouter(
        discovery,
        threshold=CalibratedThreshold(threshold, "fixture-calibration-v1"),
        top_k=2,
    )


def test_two_strategies_share_recall_but_rerank_changes_selection_without_execution():
    first, second = _candidate(FIRST_ID, 0.9), _candidate(SECOND_ID, 0.8)
    discovery = ScriptedDiscovery((first, second))
    transport = FakeTransport(_response(SECOND_ID, FIRST_ID))
    semantic = _semantic(discovery)
    semantic_llm = SemanticLLMRouter(semantic, RerankAdapter(transport))

    with patch("workflow.graph.GraphExecutor.run") as workflow_run, patch(
        "runtime.node.agent.providers.base.ProviderRegistry.get_provider"
    ) as provider_lookup:
        semantic_result = semantic.route(TASK)
        llm_result = semantic_llm.route(TASK)

    assert semantic_result.status == llm_result.status == "selected"
    assert semantic_result.strategy == "semantic"
    assert semantic_result.selected_agent == first
    assert semantic_result.rerank_result is None
    assert llm_result.strategy == "semantic_llm"
    assert llm_result.selected_agent == second
    assert llm_result.candidates == semantic_result.candidates == (first, second)
    assert [item.raw_similarity for item in llm_result.candidates] == [0.9, 0.8]
    assert llm_result.rerank_result.ordered_candidate_ids == (SECOND_ID, FIRST_ID)
    assert llm_result.rerank_result.reason_code == "CAPABILITY_MATCH"
    assert not hasattr(llm_result, "confidence")
    assert discovery.calls == [(TASK, 2), (TASK, 2)]
    assert len(transport.calls) == 1
    workflow_run.assert_not_called()
    provider_lookup.assert_not_called()


@pytest.mark.parametrize("candidates", [(), (_candidate(FIRST_ID, 0.49),)])
def test_empty_or_low_confidence_rejects_before_rerank(candidates):
    transport = FakeTransport(_response(FIRST_ID))
    result = SemanticLLMRouter(
        _semantic(ScriptedDiscovery(candidates)), RerankAdapter(transport)
    ).route(TASK)

    assert result.status == "rejected"
    assert result.strategy == "semantic_llm"
    assert result.reason_code == "NO_SUITABLE_AGENT"
    assert result.selected_agent is None
    assert result.candidates == candidates
    assert result.rerank_result is None
    assert transport.calls == []


def test_threshold_equality_calls_reranker():
    candidate = _candidate(FIRST_ID, 0.5)
    transport = FakeTransport(_response(FIRST_ID))
    result = SemanticLLMRouter(
        _semantic(ScriptedDiscovery((candidate,))), RerankAdapter(transport)
    ).route(TASK)

    assert result.status == "selected"
    assert result.selected_agent == candidate
    assert len(transport.calls) == 1


def test_discovery_failure_remains_failure_without_rerank():
    transport = FakeTransport(_response(FIRST_ID))
    discovery = ScriptedDiscovery(error=EmbeddingValidationError("ZERO_VECTOR"))
    result = SemanticLLMRouter(_semantic(discovery), RerankAdapter(transport)).route(TASK)

    assert result.status == "failed"
    assert result.strategy == "semantic_llm"
    assert result.error_code == "ZERO_VECTOR"
    assert result.selected_agent is None
    assert result.rerank_result is None
    assert transport.calls == []


@pytest.mark.parametrize(
    "response,error,code",
    [
        (_response(UUID(int=99)), None, "RERANK_UNKNOWN_CANDIDATE"),
        (_response(FIRST_ID, FIRST_ID), None, "RERANK_DUPLICATE_CANDIDATE"),
        ("{", None, "RERANK_INVALID_RESPONSE"),
        (None, TimeoutError("private token"), "RERANK_TIMEOUT"),
        (None, RuntimeError("private token"), "RERANK_SERVICE_ERROR"),
    ],
)
def test_rerank_errors_fail_without_semantic_fallback(response, error, code):
    first, second = _candidate(FIRST_ID, 0.9), _candidate(SECOND_ID, 0.8)
    transport = FakeTransport(response=response, error=error)
    result = SemanticLLMRouter(
        _semantic(ScriptedDiscovery((first, second))), RerankAdapter(transport)
    ).route(TASK)

    assert result.status == "failed"
    assert result.strategy == "semantic_llm"
    assert result.error_code == code
    assert result.selected_agent is None
    assert result.candidates == (first, second)
    assert result.rerank_result is None
    assert len(transport.calls) == 1
    assert "private token" not in repr(result)


@pytest.mark.parametrize("ids", [(), (UUID(int=99),), (FIRST_ID, FIRST_ID)])
def test_router_does_not_trust_unvalidated_reranker_ids(ids):
    first = _candidate(FIRST_ID, 0.9)

    class InvalidReranker:
        def rerank(self, task, candidates):
            return RerankResult(ids, "CAPABILITY_MATCH")

    result = SemanticLLMRouter(
        _semantic(ScriptedDiscovery((first,))), InvalidReranker()
    ).route(TASK)

    assert result.status == "failed"
    assert result.error_code == "RERANK_INVALID_RESPONSE"
    assert result.selected_agent is None
    assert result.candidates == (first,)
    assert result.rerank_result is None
