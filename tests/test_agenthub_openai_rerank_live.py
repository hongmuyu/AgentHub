"""Explicit opt-in DeepSeek rerank smoke; skipped by default."""

import os
from uuid import UUID

import pytest

from server.services.agenthub.discovery import DiscoveryCandidate, PublicAgentMetadata
from server.services.agenthub.openai_rerank import OpenAICompatibleRerankTransport
from server.services.agenthub.reranker import RerankAdapter


@pytest.mark.skipif(
    os.environ.get("AGENTHUB_RERANK_LIVE") != "1",
    reason="set AGENTHUB_RERANK_LIVE=1 for an explicitly configured live check",
)
def test_configured_live_rerank():
    candidates = tuple(DiscoveryCandidate(
        agent_id=UUID(int=index), version=1,
        public_metadata=PublicAgentMetadata(
            name=name, description=description, capabilities=(capability,),
            tags=("fixture",), tools=(),
        ), raw_similarity=0.8,
    ) for index, name, description, capability in (
        (1, "Code Agent", "Explains code", "analyze supplied Python code"),
        (2, "Document Agent", "Summarizes text", "summarize supplied documents"),
    ))
    result = RerankAdapter(OpenAICompatibleRerankTransport.from_environment()).rerank(
        "Explain what the supplied Python function does", candidates,
    )
    assert result.ordered_candidate_ids
    assert set(result.ordered_candidate_ids) <= {candidate.agent_id for candidate in candidates}
