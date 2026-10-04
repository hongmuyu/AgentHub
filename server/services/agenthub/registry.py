"""Business Agent registration and administrative directory reads."""

from typing import Literal, Mapping
from uuid import UUID

from .database import AgentHubDatabase
from .discovery import prepare_embeddings
from .embedding_index import AgentEmbeddingRepository
from .embeddings import EmbeddingBackend
from .metadata import AgentMetadata, AgentMetadataInput
from .thin_workflow import ThinWorkflowValidator
from .versions import AgentVersionRepository


Status = Literal["active", "disabled"]


class AgentRegistry:
    """Expose only registered, validated, indexed Agent versions."""

    def __init__(
        self,
        database: AgentHubDatabase,
        validator: ThinWorkflowValidator,
        backend: EmbeddingBackend,
    ) -> None:
        self.database = database
        self.validator = validator
        self.backend = backend
        self.versions = AgentVersionRepository(database)
        self.index = AgentEmbeddingRepository(database)

    def register(self, content: AgentMetadataInput | Mapping[str, object]) -> AgentMetadata:
        metadata = AgentMetadataInput.model_validate(content)
        self.validator.validate(metadata.runtime_ref)
        agent = AgentMetadata.register(metadata)
        embedding = prepare_embeddings((agent.snapshot,), self.backend)[0]
        with self.database.transaction() as connection:
            self.versions.insert_initial(connection, agent)
            self.index.insert_many(connection, (embedding,))
        return agent

    def update(
        self, agent_id: UUID, content: AgentMetadataInput | Mapping[str, object]
    ) -> AgentMetadata:
        current = self.get(agent_id)
        if current is None:
            raise LookupError("Agent identity does not exist")
        metadata = AgentMetadataInput.model_validate(content)
        self.validator.validate(metadata.runtime_ref)
        updated = current.update_content(metadata)
        embedding = prepare_embeddings((updated.snapshot,), self.backend)[0]
        with self.database.transaction() as connection:
            self.versions.insert_next_snapshot(
                connection, updated.snapshot, updated.updated_at
            )
            self.index.insert_many(connection, (embedding,))
            self.versions.switch_current(connection, updated.snapshot, updated.updated_at)
        return updated

    def get(self, agent_id: UUID) -> AgentMetadata | None:
        return self.versions.get_current(agent_id)

    def list(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        include_disabled: bool = False,
        status: Status | None = None,
    ) -> tuple[AgentMetadata, ...]:
        self._validate_page(limit, offset, status)
        selected_status = status
        if selected_status is None and not include_disabled:
            selected_status = "active"
        return self.versions.list_current(selected_status)[offset:offset + limit]

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        include_disabled: bool = False,
        status: Status | None = None,
    ) -> tuple[AgentMetadata, ...]:
        self._validate_page(limit, offset, status)
        if not isinstance(query, str):
            raise ValueError("search query must be text")
        needle = " ".join(query.split()).casefold()
        if not needle:
            return ()
        selected_status = status
        if selected_status is None and not include_disabled:
            selected_status = "active"
        matches = (
            agent for agent in self.versions.list_current(selected_status)
            if needle in " ".join((
                agent.snapshot.name,
                agent.snapshot.description,
                *agent.snapshot.capabilities,
                *agent.snapshot.tags,
                *agent.snapshot.tools,
            )).casefold()
        )
        return tuple(matches)[offset:offset + limit]

    @staticmethod
    def _validate_page(limit: int, offset: int, status: Status | None) -> None:
        if type(limit) is not int or limit <= 0 or type(offset) is not int or offset < 0:
            raise ValueError("limit must be positive and offset must be non-negative")
        if status not in (None, "active", "disabled"):
            raise ValueError("status must be active or disabled")
