"""Explicit full-catalog re-embedding with one atomic active-index switch."""

from .database import AgentHubDatabase
from .discovery import prepare_embeddings
from .embedding_index import AgentEmbeddingRepository
from .embeddings import EmbeddingBackend
from .versions import AgentVersionRepository


class IndexSnapshotChangedError(RuntimeError):
    def __init__(self) -> None:
        self.code = "INDEX_SNAPSHOT_CHANGED"
        super().__init__(self.code)


class AgentIndexRebuilder:
    def __init__(self, database: AgentHubDatabase) -> None:
        self.database = database
        self.versions = AgentVersionRepository(database)
        self.index = AgentEmbeddingRepository(database)

    def rebuild(self, backend: EmbeddingBackend) -> None:
        """Prepare all current versions, then publish only if the directory is unchanged."""
        snapshots = self.versions.list_current()
        expected_directory = tuple(
            (str(agent.snapshot.id), agent.snapshot.version, agent.status) for agent in snapshots
        )
        previous = self.index.get_active()
        target = (backend.model_key, backend.dimensions)
        if previous is not None and previous[0] == target[0]:
            raise ValueError("target embedding model key must differ from active model key")
        records = prepare_embeddings(tuple(agent.snapshot for agent in snapshots), backend)
        if any((record.embedding_model_key, record.dimensions) != target for record in records):
            raise ValueError("embedding backend identity changed during rebuild")

        with self.database.transaction() as connection:
            current_directory = tuple(
                connection.execute(
                    "SELECT agent_id, current_version, status FROM agenthub_agents ORDER BY agent_id"
                ).fetchall()
            )
            if current_directory != expected_directory:
                raise IndexSnapshotChangedError()
            if self.index.get_active(connection) != previous:
                raise IndexSnapshotChangedError()
            self.index.insert_many(connection, records)
            self.index.switch_active(
                connection, expected=previous, model_key=target[0], dimensions=target[1]
            )
