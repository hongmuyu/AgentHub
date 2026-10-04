"""Rerank only recalled public candidates through an injected transport."""

import json
from unittest.mock import patch
from uuid import UUID

import pytest

from server.services.agenthub.discovery import DiscoveryCandidate, PublicAgentMetadata
from server.services.agenthub.reranker import RerankAdapter, RerankError


TASK = "Compare the reports"
IDS = (UUID(int=1), UUID(int=2), UUID(int=3))


def _candidate(number):
    return DiscoveryCandidate(
        agent_id=IDS[number - 1],
        version=number,
        public_metadata=PublicAgentMetadata(
            name=f"Agent {number}",
            description=f"Public description {number}",
            capabilities=(f"capability {number}",),
            tags=("internal",),
            tools=("private_tool_marker",),
        ),
        raw_similarity=0.9 - number * 0.1,
    )


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def _response(ids, reason_code="CAPABILITY_MATCH"):
    return json.dumps({"candidate_ids": [str(agent_id) for agent_id in ids],
                       "reason_code": reason_code})


def test_transport_gets_only_task_and_public_top_k_fields_without_execution():
    candidates = (_candidate(1), _candidate(2))
    transport = FakeTransport(_response((IDS[1], IDS[0])))
    with patch("workflow.graph.GraphExecutor.run") as workflow_run, patch(
        "runtime.node.agent.providers.base.ProviderRegistry.get_provider"
    ) as provider_lookup:
        result = RerankAdapter(transport).rerank(TASK, candidates)

    assert transport.requests == [{
        "task": TASK,
        "candidates": [
            {"id": str(IDS[0]), "name": "Agent 1",
             "description": "Public description 1",
             "capabilities": ["capability 1"], "tags": ["internal"]},
            {"id": str(IDS[1]), "name": "Agent 2",
             "description": "Public description 2",
             "capabilities": ["capability 2"], "tags": ["internal"]},
        ],
    }]
    assert result.ordered_candidate_ids == (IDS[1], IDS[0])
    assert result.reason_code == "CAPABILITY_MATCH"
    assert "private_tool_marker" not in str(transport.requests)
    assert "raw_similarity" not in str(transport.requests)
    assert "version" not in str(transport.requests)
    workflow_run.assert_not_called()
    provider_lookup.assert_not_called()


def test_nonempty_subset_preserves_model_order():
    transport = FakeTransport(_response((IDS[2], IDS[0]), reason_code="CROSS_CAPABILITY"))
    result = RerankAdapter(transport).rerank(TASK, tuple(_candidate(i) for i in (1, 2, 3)))

    assert result.ordered_candidate_ids == (IDS[2], IDS[0])
    assert result.reason_code == "CROSS_CAPABILITY"


@pytest.mark.parametrize(
    "response,code",
    [
        (_response(()), "RERANK_INVALID_RESPONSE"),
        (_response((UUID(int=99),)), "RERANK_UNKNOWN_CANDIDATE"),
        (_response((IDS[0], IDS[0])), "RERANK_DUPLICATE_CANDIDATE"),
        ("{", "RERANK_INVALID_RESPONSE"),
        ("[]", "RERANK_INVALID_RESPONSE"),
        (json.dumps({"candidate_ids": str(IDS[0]), "reason_code": "MATCH"}),
         "RERANK_INVALID_RESPONSE"),
        (json.dumps({"candidate_ids": [1], "reason_code": "MATCH"}),
         "RERANK_INVALID_RESPONSE"),
        (_response((IDS[0],), reason_code="free form explanation"),
         "RERANK_INVALID_RESPONSE"),
        (json.dumps({"candidate_ids": [str(IDS[0])], "reason_code": "MATCH",
                     "reasoning": "private chain of thought"}),
         "RERANK_INVALID_RESPONSE"),
        (f'{{"candidate_ids": ["{IDS[0]}"], "reason_code": "MATCH", '
         f'"candidate_ids": ["{IDS[1]}"]}}', "RERANK_INVALID_RESPONSE"),
    ],
)
def test_invalid_response_is_rejected_without_exposing_model_text(response, code):
    transport = FakeTransport(response)
    with pytest.raises(RerankError) as error:
        RerankAdapter(transport).rerank(TASK, (_candidate(1), _candidate(2)))

    assert error.value.code == code
    assert str(error.value) == code
    assert response not in repr(error.value)


@pytest.mark.parametrize(
    "error,code",
    [
        (TimeoutError("private endpoint and token"), "RERANK_TIMEOUT"),
        (RuntimeError("private endpoint and token"), "RERANK_SERVICE_ERROR"),
    ],
)
def test_transport_failures_have_safe_error_codes(error, code):
    transport = FakeTransport(error=error)
    with pytest.raises(RerankError) as raised:
        RerankAdapter(transport).rerank(TASK, (_candidate(1),))

    assert raised.value.code == code
    assert "private endpoint and token" not in str(raised.value)
    assert "private endpoint and token" not in repr(raised.value)


@pytest.mark.parametrize(
    "task,candidates",
    [
        (TASK, ()),
        (TASK, (_candidate(1), _candidate(1))),
        (" ", (_candidate(1),)),
    ],
)
def test_unselectable_input_never_calls_transport(task, candidates):
    transport = FakeTransport(_response((IDS[0],)))
    with pytest.raises(RerankError) as error:
        RerankAdapter(transport).rerank(task, candidates)

    assert error.value.code == "RERANK_INVALID_REQUEST"
    assert transport.requests == []
