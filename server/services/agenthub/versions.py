"""Persistent Agent identity and metadata versions."""

import sqlite3
from datetime import datetime, timezone
from uuid import UUID

from .database import AgentHubDatabase
from .metadata import AgentMetadata, AgentMetadataInput, AgentMetadataVersion


class AgentVersionRepository:
    """Store version snapshots and the identity's current version separately."""

    def __init__(self, database: AgentHubDatabase) -> None:
        self.database = database
        self.initialize()

    def initialize(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agenthub_agents (
                    agent_id TEXT PRIMARY KEY,
                    current_version INTEGER NOT NULL CHECK (current_version >= 1),
                    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (agent_id, current_version)
                        REFERENCES agenthub_agent_versions (agent_id, version)
                        DEFERRABLE INITIALLY DEFERRED
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agenthub_agent_versions (
                    agent_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version >= 1),
                    content_json TEXT NOT NULL,
                    PRIMARY KEY (agent_id, version),
                    FOREIGN KEY (agent_id) REFERENCES agenthub_agents (agent_id)
                        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS agenthub_versions_no_replace
                BEFORE INSERT ON agenthub_agent_versions
                WHEN EXISTS (
                    SELECT 1 FROM agenthub_agent_versions
                    WHERE agent_id = NEW.agent_id AND version = NEW.version
                )
                BEGIN SELECT RAISE(ABORT, 'Agent versions are immutable'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS agenthub_versions_no_update
                BEFORE UPDATE ON agenthub_agent_versions
                BEGIN SELECT RAISE(ABORT, 'Agent versions are immutable'); END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS agenthub_versions_no_delete
                BEFORE DELETE ON agenthub_agent_versions
                BEGIN SELECT RAISE(ABORT, 'Agent versions are immutable'); END
                """
            )

    @staticmethod
    def _require_transaction(connection: sqlite3.Connection) -> None:
        if not connection.in_transaction:
            raise RuntimeError("Agent version writes require an explicit transaction")

    def insert_initial(self, connection: sqlite3.Connection, agent: AgentMetadata) -> None:
        self._require_transaction(connection)
        if agent.snapshot.version != 1:
            raise ValueError("initial Agent version must be 1")
        connection.execute(
            """
            INSERT INTO agenthub_agents
                (agent_id, current_version, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(agent.snapshot.id),
                1,
                agent.status,
                agent.created_at.isoformat(),
                agent.updated_at.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO agenthub_agent_versions (agent_id, version, content_json)
            VALUES (?, ?, ?)
            """,
            (
                str(agent.snapshot.id),
                1,
                agent.snapshot.model_dump_json(exclude={"id", "version"}),
            ),
        )

    def insert_next(
        self,
        connection: sqlite3.Connection,
        snapshot: AgentMetadataVersion,
        updated_at: datetime,
    ) -> None:
        self.insert_next_snapshot(connection, snapshot, updated_at)
        self.switch_current(connection, snapshot, updated_at)

    def insert_next_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot: AgentMetadataVersion,
        updated_at: datetime,
    ) -> None:
        """Stage the next immutable version without exposing it as current."""
        self._require_transaction(connection)
        row = connection.execute(
            "SELECT current_version, updated_at FROM agenthub_agents WHERE agent_id = ?",
            (str(snapshot.id),),
        ).fetchone()
        if row is None:
            raise LookupError("Agent identity does not exist")
        if snapshot.version != row[0] + 1:
            raise ValueError("snapshot must be the next version")
        if updated_at.tzinfo is None or updated_at.utcoffset() is None:
            raise ValueError("updated_at must include a UTC offset")
        if updated_at.astimezone(timezone.utc) < datetime.fromisoformat(row[1]):
            raise ValueError("updated_at must not precede the current timestamp")

        connection.execute(
            """
            INSERT INTO agenthub_agent_versions (agent_id, version, content_json)
            VALUES (?, ?, ?)
            """,
            (
                str(snapshot.id),
                snapshot.version,
                snapshot.model_dump_json(exclude={"id", "version"}),
            ),
        )

    def switch_current(
        self,
        connection: sqlite3.Connection,
        snapshot: AgentMetadataVersion,
        updated_at: datetime,
    ) -> None:
        """Switch the identity pointer after its new version is ready."""
        self._require_transaction(connection)
        row = connection.execute(
            "SELECT current_version, updated_at FROM agenthub_agents WHERE agent_id = ?",
            (str(snapshot.id),),
        ).fetchone()
        if row is None:
            raise LookupError("Agent identity does not exist")
        if snapshot.version != row[0] + 1:
            raise ValueError("snapshot must be the next version")
        if updated_at.tzinfo is None or updated_at.utcoffset() is None:
            raise ValueError("updated_at must include a UTC offset")
        timestamp = updated_at.astimezone(timezone.utc)
        if timestamp < datetime.fromisoformat(row[1]):
            raise ValueError("updated_at must not precede the current timestamp")
        cursor = connection.execute(
            """
            UPDATE agenthub_agents SET current_version = ?, updated_at = ?
            WHERE agent_id = ? AND current_version = ?
            """,
            (snapshot.version, timestamp.isoformat(), str(snapshot.id), row[0]),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Agent current version changed during update")

    def get_current(self, agent_id: UUID) -> AgentMetadata | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT a.status, a.created_at, a.updated_at, v.version, v.content_json
                FROM agenthub_agents AS a
                JOIN agenthub_agent_versions AS v
                  ON v.agent_id = a.agent_id AND v.version = a.current_version
                WHERE a.agent_id = ?
                """,
                (str(agent_id),),
            ).fetchone()
        if row is None:
            return None
        return AgentMetadata(
            snapshot=self._snapshot(agent_id, row[3], row[4]),
            status=row[0],
            created_at=row[1],
            updated_at=row[2],
        )

    def get_version(self, agent_id: UUID, version: int) -> AgentMetadataVersion | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT content_json FROM agenthub_agent_versions
                WHERE agent_id = ? AND version = ?
                """,
                (str(agent_id), version),
            ).fetchone()
        if row is None:
            return None
        return self._snapshot(agent_id, version, row[0])

    def list_current(self, status: str | None = None) -> tuple[AgentMetadata, ...]:
        """Read current directory entries in stable identity order."""
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT a.agent_id, a.status, a.created_at, a.updated_at,
                       v.version, v.content_json
                FROM agenthub_agents AS a
                JOIN agenthub_agent_versions AS v
                  ON v.agent_id = a.agent_id AND v.version = a.current_version
                WHERE (? IS NULL OR a.status = ?)
                ORDER BY a.agent_id
                """,
                (status, status),
            ).fetchall()
        return tuple(
            AgentMetadata(
                snapshot=self._snapshot(UUID(row[0]), row[4], row[5]),
                status=row[1],
                created_at=row[2],
                updated_at=row[3],
            )
            for row in rows
        )

    def update_status(
        self, connection: sqlite3.Connection, before: AgentMetadata, after: AgentMetadata
    ) -> None:
        """Change only identity status if its validated version is still current."""
        self._require_transaction(connection)
        if (
            before.snapshot != after.snapshot
            or before.created_at != after.created_at
            or before.status == after.status
            or after.updated_at <= before.updated_at
        ):
            raise ValueError("status update must preserve the version and advance time")
        cursor = connection.execute(
            """
            UPDATE agenthub_agents SET status = ?, updated_at = ?
            WHERE agent_id = ? AND current_version = ? AND status = ? AND updated_at = ?
            """,
            (
                after.status,
                after.updated_at.isoformat(),
                str(before.snapshot.id),
                before.snapshot.version,
                before.status,
                before.updated_at.isoformat(),
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Agent changed during status update")

    @staticmethod
    def _snapshot(agent_id: UUID, version: int, content_json: str) -> AgentMetadataVersion:
        content = AgentMetadataInput.model_validate_json(content_json)
        return AgentMetadataVersion(**content.model_dump(), id=agent_id, version=version)
