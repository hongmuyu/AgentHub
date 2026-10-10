"""Explicit opt-in live check; skipped by the default test run."""

import os

import pytest

from server.services.agenthub.embeddings import checked_embed
from server.services.agenthub.openai_embeddings import OpenAICompatibleEmbeddingBackend


@pytest.mark.skipif(
    os.environ.get("AGENTHUB_EMBEDDING_LIVE") != "1",
    reason="set AGENTHUB_EMBEDDING_LIVE=1 to run an explicitly configured live check",
)
def test_configured_live_embedding():
    backend = OpenAICompatibleEmbeddingBackend.from_environment()
    result = checked_embed(backend, ["AgentHub embedding live check"])
    assert len(result.vectors) == 1
    assert len(result.vectors[0]) == backend.dimensions
