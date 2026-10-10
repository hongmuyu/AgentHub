"""SQLite persistence for versioned Agent capability embeddings."""

import json
import sqlite3
from typing import Sequence
from uuid import UUID

from .database import AgentHubDatabase
from .discovery import AgentEmbedding, metadata_hash
from .embeddings import validate_vector
from .metadata import AgentMetadataInput


class ActiveIndexMismatchError(ValueError):
    def __init__(self) -> None:
        self.code = "INDEX_MODEL_MISMATCH"
        super().__init__(self.code)


class AgentEmbeddingRepository:
    def __init__(self, database: AgentHubDatabase) -> None:
        self.database = database
        self.initialize()

    def initialize(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agenthub_agent_embeddings (
                    agent_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version >= 1),
                    embedding_model_key TEXT NOT NULL,
                    metadata_hash TEXT NOT NULL,
                    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
                    vector_json TEXT NOT NULL,
                    PRIMARY KEY (agent_id, version, embedding_model_key),
                    FOREIGN KEY (agent_id, version)
                        REFERENCES agenthub_agent_versions (agent_id, version)
                        ON DELETE RESTRICT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agenthub_active_embedding_index (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    embedding_model_key TEXT NOT NULL,
                    dimensions INTEGER NOT NULL CHECK (dimensions > 0)
                )
                """
            )

    def get_active(self, connection: sqlite3.Connection | None = None) -> tuple[str, int] | None:
        if connection is None:
            with self.database.connection() as opened:
                return self.get_active(opened)
        row = connection.execute(
            "SELECT embedding_model_key, dimensions FROM agenthub_active_embedding_index "
            "WHERE singleton = 1"
        ).fetchone()
        return (row[0], row[1]) if row is not None else None

    def require_backend(
        self, model_key: str, dimensions: int, connection: sqlite3.Connection | None = None
    ) -> None:
        active = self.get_active(connection)
        if active is not None and active != (model_key, dimensions):
            raise ActiveIndexMismatchError()

    def ensure_active(self, connection: sqlite3.Connection, model_key: str, dimensions: int) -> None:
        if not connection.in_transaction:
            raise RuntimeError("active index writes require an explicit transaction")
        self.require_backend(model_key, dimensions, connection)
        if self.get_active(connection) is None:
            rows = connection.execute(
                """
                SELECT v.content_json, e.metadata_hash, e.dimensions, e.vector_json
                FROM agenthub_agents AS a
                JOIN agenthub_agent_versions AS v
                  ON v.agent_id = a.agent_id AND v.version = a.current_version
                LEFT JOIN agenthub_agent_embeddings AS e
                  ON e.agent_id = a.agent_id AND e.version = a.current_version
                 AND e.embedding_model_key = ?
                """,
                (model_key,),
            ).fetchall()
            for content_json, stored_hash, stored_dimensions, vector_json in rows:
                current_hash = metadata_hash(AgentMetadataInput.model_validate_json(content_json))
                if (
                    stored_hash != current_hash
                    or stored_dimensions != dimensions
                ):
                    raise ActiveIndexMismatchError()
                try:
                    validate_vector(
                        json.loads(vector_json), model_key=model_key, dimensions=dimensions
                    )
                except (TypeError, ValueError):
                    raise ActiveIndexMismatchError() from None
            connection.execute(
                "INSERT INTO agenthub_active_embedding_index "
                "(singleton, embedding_model_key, dimensions) VALUES (1, ?, ?)",
                (model_key, dimensions),
            )

    def switch_active(
        self,
        connection: sqlite3.Connection,
        *,
        expected: tuple[str, int] | None,
        model_key: str,
        dimensions: int,
    ) -> None:
        if not connection.in_transaction:
            raise RuntimeError("active index writes require an explicit transaction")
        if self.get_active(connection) != expected:
            raise ActiveIndexMismatchError()
        if expected is None:
            connection.execute(
                "INSERT INTO agenthub_active_embedding_index "
                "(singleton, embedding_model_key, dimensions) VALUES (1, ?, ?)",
                (model_key, dimensions),
            )
        else:
            connection.execute(
                "UPDATE agenthub_active_embedding_index "
                "SET embedding_model_key = ?, dimensions = ? WHERE singleton = 1",
                (model_key, dimensions),
            )

    def insert_many(
        self, connection: sqlite3.Connection, records: Sequence[AgentEmbedding]
    ) -> None:
        """Join an existing version transaction; never commit a partial batch."""
        if not connection.in_transaction:
            raise RuntimeError("Agent embedding writes require an explicit transaction")
        connection.execute("SAVEPOINT agenthub_embedding_batch")
        try:
            for record in records:
                vector = validate_vector(
                    record.vector,
                    model_key=record.embedding_model_key,
                    dimensions=record.dimensions,
                )
                row = connection.execute(
                    """
                    SELECT content_json FROM agenthub_agent_versions
                    WHERE agent_id = ? AND version = ?
                    """,
                    (str(record.agent_id), record.version),
                ).fetchone()
                if row is None:
                    raise ValueError("Agent embedding version does not exist")
                if metadata_hash(AgentMetadataInput.model_validate_json(row[0])) != record.metadata_hash:
                    raise ValueError("Agent embedding metadata hash does not match version")
                connection.execute(
                    """
                    INSERT INTO agenthub_agent_embeddings
                        (agent_id, version, embedding_model_key, metadata_hash,
                         dimensions, vector_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(record.agent_id), record.version, record.embedding_model_key,
                        record.metadata_hash, record.dimensions, json.dumps(vector, allow_nan=False),
                    ),
                )
        except BaseException:
            connection.execute("ROLLBACK TO SAVEPOINT agenthub_embedding_batch")
            raise
        finally:
            connection.execute("RELEASE SAVEPOINT agenthub_embedding_batch")

    def get(self, agent_id: UUID, version: int, model_key: str) -> AgentEmbedding | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT metadata_hash, dimensions, vector_json
                FROM agenthub_agent_embeddings
                WHERE agent_id = ? AND version = ? AND embedding_model_key = ?
                """,
                (str(agent_id), version, model_key),
            ).fetchone()
        if row is None:
            return None
        return AgentEmbedding(
            agent_id=agent_id,
            version=version,
            embedding_model_key=model_key,
            metadata_hash=row[0],
            dimensions=row[1],
            vector=validate_vector(json.loads(row[2]), model_key=model_key, dimensions=row[1]),
        )
