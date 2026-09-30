"""Canonical public Agent capability text and prepared version embeddings."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Sequence
from uuid import UUID

from .embeddings import EmbeddingBackend, Vector, checked_embed
from .metadata import AgentMetadataInput, AgentMetadataVersion


def discovery_text(metadata: AgentMetadataInput) -> str:
    """Include only validated public fields, with capabilities first."""
    lines = [f"capability: {capability}" for capability in metadata.capabilities]
    lines.extend((f"name: {metadata.name}", f"description: {metadata.description}"))
    lines.extend(f"tag: {tag}" for tag in metadata.tags)
    lines.extend(f"tool: {tool}" for tool in metadata.tools)
    return "\n".join(lines)


def metadata_hash(metadata: AgentMetadataInput) -> str:
    return sha256(discovery_text(metadata).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentEmbedding:
    agent_id: UUID
    version: int
    embedding_model_key: str
    metadata_hash: str
    dimensions: int
    vector: Vector


def prepare_embeddings(
    snapshots: Sequence[AgentMetadataVersion], backend: EmbeddingBackend
) -> tuple[AgentEmbedding, ...]:
    """Validate the entire ordered batch before creating index records."""
    ordered = tuple(snapshots)
    texts = tuple(discovery_text(snapshot) for snapshot in ordered)
    batch = checked_embed(backend, texts)
    return tuple(
        AgentEmbedding(
            agent_id=snapshot.id,
            version=snapshot.version,
            embedding_model_key=batch.model_key,
            metadata_hash=sha256(text.encode("utf-8")).hexdigest(),
            dimensions=batch.dimensions,
            vector=vector,
        )
        for snapshot, text, vector in zip(ordered, texts, batch.vectors, strict=True)
    )
