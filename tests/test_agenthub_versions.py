"""Agent identity and immutable metadata version persistence."""

import json
import sqlite3
from uuid import uuid4

import pytest
from pydantic import ValidationError

from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.metadata import AgentMetadata, AgentMetadataInput
from server.services.agenthub.versions import AgentVersionRepository


def _agent(name: str = "Research Agent") -> AgentMetadata:
    return AgentMetadata.register(
        AgentMetadataInput(
            name=name,
            description="Searches technical information",
            capabilities=["Search technical docs"],
            runtime_ref="workflow://research-agent/1",
        )
    )


def _stored(tmp_path, *, disabled: bool = False):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    repository = AgentVersionRepository(database)
    agent = _agent()
    if disabled:
        agent = agent.set_status("disabled")
    with database.transaction() as connection:
        repository.insert_initial(connection, agent)
    return database, repository, agent


def _updated(agent: AgentMetadata) -> AgentMetadata:
    return agent.update_content(
        AgentMetadataInput(
            name="Updated Research Agent",
            description="Searches and compares technical information",
            capabilities=["Compare technical docs"],
            runtime_ref="workflow://research-agent/2",
        )
    )


def test_initial_version_round_trips_after_database_reopen(tmp_path):
    path = tmp_path / "agenthub.db"
    database = AgentHubDatabase(path)
    repository = AgentVersionRepository(database)
    repository.initialize()
    repository.initialize()
    agent = _agent()

    with database.transaction() as connection:
        repository.insert_initial(connection, agent)

    reopened = AgentVersionRepository(AgentHubDatabase(path))
    assert reopened.get_current(agent.snapshot.id) == agent
    assert reopened.get_version(agent.snapshot.id, 1) == agent.snapshot
    assert reopened.get_version(agent.snapshot.id, 2) is None
    assert reopened.get_current(uuid4()) is None


def test_next_version_switches_current_and_preserves_disabled_status(tmp_path):
    database, repository, initial = _stored(tmp_path, disabled=True)
    updated = _updated(initial)

    with database.transaction() as connection:
        repository.insert_next(connection, updated.snapshot, updated.updated_at)

    assert repository.get_current(initial.snapshot.id) == updated
    assert repository.get_version(initial.snapshot.id, 1) == initial.snapshot
    assert repository.get_version(initial.snapshot.id, 2) == updated.snapshot
    assert repository.get_current(initial.snapshot.id).status == "disabled"

    reopened = AgentVersionRepository(AgentHubDatabase(database.path))
    assert reopened.get_current(initial.snapshot.id) == updated
    assert reopened.get_version(initial.snapshot.id, 1) == initial.snapshot


def test_later_failure_rolls_back_version_insert_and_current_switch(tmp_path):
    database, repository, initial = _stored(tmp_path)
    updated = _updated(initial)

    with pytest.raises(RuntimeError, match="later index failure"):
        with database.transaction() as connection:
            repository.insert_next(connection, updated.snapshot, updated.updated_at)
            raise RuntimeError("later index failure")

    assert repository.get_current(initial.snapshot.id) == initial
    assert repository.get_version(initial.snapshot.id, 2) is None


def test_duplicate_version_and_skipped_version_are_rejected(tmp_path):
    database, repository, initial = _stored(tmp_path)
    with database.connection() as connection:
        content = connection.execute(
            "SELECT content_json FROM agenthub_agent_versions WHERE agent_id = ? AND version = 1",
            (str(initial.snapshot.id),),
        ).fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO agenthub_agent_versions (agent_id, version, content_json) "
                "VALUES (?, 1, ?)",
                (str(initial.snapshot.id), content),
            )

    skipped = _updated(initial).snapshot.model_copy(update={"version": 3})
    with pytest.raises(ValueError, match="next version"):
        with database.transaction() as connection:
            repository.insert_next(connection, skipped, initial.updated_at)

    assert repository.get_current(initial.snapshot.id) == initial
    assert repository.get_version(initial.snapshot.id, 3) is None


def test_history_cannot_be_updated_or_deleted(tmp_path):
    database, repository, initial = _stored(tmp_path)
    updated = _updated(initial)
    with database.transaction() as connection:
        repository.insert_next(connection, updated.snapshot, updated.updated_at)

    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                "UPDATE agenthub_agent_versions SET content_json = '{}' "
                "WHERE agent_id = ? AND version = 1",
                (str(initial.snapshot.id),),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                "DELETE FROM agenthub_agent_versions WHERE agent_id = ? AND version = 1",
                (str(initial.snapshot.id),),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO agenthub_agent_versions "
                "(agent_id, version, content_json) VALUES (?, 1, '{}')",
                (str(initial.snapshot.id),),
            )

    assert repository.get_version(initial.snapshot.id, 1) == initial.snapshot
    assert repository.get_current(initial.snapshot.id) == updated


def test_current_pointer_must_reference_an_existing_version(tmp_path):
    database, repository, initial = _stored(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                "UPDATE agenthub_agents SET current_version = 99 WHERE agent_id = ?",
                (str(initial.snapshot.id),),
            )
    assert repository.get_current(initial.snapshot.id) == initial


def test_content_json_cannot_override_identity_columns(tmp_path):
    database, repository, initial = _stored(tmp_path)
    content = initial.snapshot.model_dump(mode="json", exclude={"id", "version"})
    content.update({"id": str(uuid4()), "version": 3})
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO agenthub_agent_versions (agent_id, version, content_json) "
            "VALUES (?, 2, ?)",
            (str(initial.snapshot.id), json.dumps(content)),
        )

    with pytest.raises(ValidationError):
        repository.get_version(initial.snapshot.id, 2)
    assert repository.get_current(initial.snapshot.id) == initial


def test_writes_require_an_explicit_transaction(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    repository = AgentVersionRepository(database)
    initial = _agent()
    with database.connection() as connection:
        with pytest.raises(RuntimeError, match="transaction"):
            repository.insert_initial(connection, initial)
    assert repository.get_current(initial.snapshot.id) is None

    with database.transaction() as connection:
        repository.insert_initial(connection, initial)
    updated = _updated(initial)
    with database.connection() as connection:
        with pytest.raises(RuntimeError, match="transaction"):
            repository.insert_next(connection, updated.snapshot, updated.updated_at)
    assert repository.get_current(initial.snapshot.id) == initial
    assert repository.get_version(initial.snapshot.id, 2) is None


def test_initial_insert_requires_version_one(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    repository = AgentVersionRepository(database)
    advanced = _updated(_agent())
    with pytest.raises(ValueError, match="version must be 1"):
        with database.transaction() as connection:
            repository.insert_initial(connection, advanced)
    assert repository.get_current(advanced.snapshot.id) is None
