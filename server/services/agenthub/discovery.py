"""Canonical Agent capability text, embeddings, and exact semantic discovery."""

import math
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Sequence
from uuid import UUID

from .embeddings import EmbeddingBackend, Vector, checked_embed
from .metadata import AgentMetadataInput, AgentMetadataVersion
from .runtime_resolver import RuntimeReferenceError
from .thin_workflow import WorkflowValidationError

if TYPE_CHECKING:
    from .embedding_index import AgentEmbeddingRepository
    from .thin_workflow import ThinWorkflowValidator
    from .versions import AgentVersionRepository


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


@dataclass(frozen=True)
class PublicAgentMetadata:
    name: str
    description: str
    capabilities: tuple[str, ...]
    tags: tuple[str, ...]
    tools: tuple[str, ...]


@dataclass(frozen=True)
class DiscoveryCandidate:
    agent_id: UUID
    version: int
    public_metadata: PublicAgentMetadata
    raw_similarity: float


class AgentDiscovery:
    """Rank the current eligible catalog without executing a workflow."""

    def __init__(
        self,
        versions: "AgentVersionRepository",
        index: "AgentEmbeddingRepository",
        validator: "ThinWorkflowValidator",
        backend: EmbeddingBackend,
    ) -> None:
        self.versions = versions
        self.index = index
        self.validator = validator
        self.backend = backend

    def discover(self, task: str, *, k: int) -> tuple[DiscoveryCandidate, ...]:
        if type(k) is not int or k <= 0:
            raise ValueError("k must be positive")
        self.index.require_backend(self.backend.model_key, self.backend.dimensions)

        eligible: list[tuple[AgentMetadataVersion, AgentEmbedding]] = []
        for agent in self.versions.list_current("active"):
            snapshot = agent.snapshot
            try:
                self.validator.validate(snapshot.runtime_ref)
            except RuntimeReferenceError as error:
                if error.code == "INVALID_WORKFLOW_MANIFEST":
                    raise
                continue
            except WorkflowValidationError:
                continue

            try:
                embedding = self.index.get(snapshot.id, snapshot.version, self.backend.model_key)
            except (TypeError, ValueError):
                continue
            if (
                embedding is None
                or embedding.dimensions != self.backend.dimensions
                or embedding.metadata_hash != metadata_hash(snapshot)
            ):
                continue
            eligible.append((snapshot, embedding))

        if not eligible:
            return ()

        query = checked_embed(self.backend, (task,)).vectors[0]
        query_norm = math.hypot(*query)
        candidates: list[DiscoveryCandidate] = []
        for snapshot, embedding in eligible:
            vector_norm = math.hypot(*embedding.vector)
            candidates.append(
                DiscoveryCandidate(
                    agent_id=snapshot.id,
                    version=snapshot.version,
                    public_metadata=PublicAgentMetadata(
                        name=snapshot.name,
                        description=snapshot.description,
                        capabilities=snapshot.capabilities,
                        tags=snapshot.tags,
                        tools=snapshot.tools,
                    ),
                    raw_similarity=math.fsum(
                        (left / query_norm) * (right / vector_norm)
                        for left, right in zip(query, embedding.vector, strict=True)
                    ),
                )
            )
        candidates.sort(key=lambda candidate: (
            -candidate.raw_similarity, str(candidate.agent_id), candidate.version
        ))
        return tuple(candidates[:k])
