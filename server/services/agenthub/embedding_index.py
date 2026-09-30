"""SQLite persistence for versioned Agent capability embeddings."""

import json
import sqlite3
from typing import Sequence
from uuid import UUID

from .database import AgentHubDatabase
from .discovery import AgentEmbedding, metadata_hash
from .embeddings import validate_vector
from .metadata import AgentMetadataInput


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
