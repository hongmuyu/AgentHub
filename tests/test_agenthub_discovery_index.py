"""Canonical public discovery text and versioned embedding persistence."""

import sqlite3
from dataclasses import replace

import pytest
from pydantic import ValidationError

from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.discovery import discovery_text, metadata_hash, prepare_embeddings
from server.services.agenthub.embedding_index import AgentEmbeddingRepository
from server.services.agenthub.embeddings import EmbeddingValidationError, FakeEmbeddingBackend
from server.services.agenthub.metadata import AgentMetadata, AgentMetadataInput
from server.services.agenthub.versions import AgentVersionRepository


def _content(**changes):
    values = dict(
        name="Research Agent",
        description="Searches technical information",
        capabilities=("Search technical docs", "Compare technologies"),
        tags=("research",),
        tools=("web_search",),
        runtime_ref="workflow://research-agent/1",
    )
    values.update(changes)
    return AgentMetadataInput(**values)


def _backend(*snapshots):
    texts = [discovery_text(snapshot) for snapshot in snapshots]
    rows = [(float(index + 1), 1.0) for index in range(len(texts))]
    return FakeEmbeddingBackend(dict(zip(texts, rows)), model_key="fake-v1", dimensions=2)


def test_canonical_text_and_hash_use_normalized_public_fields_only():
    first = _content()
    equivalent = _content(
        name="  Research   Agent ",
        description=" Searches  technical   information ",
        capabilities=(" Search  technical docs ", "Compare technologies"),
        tags=("RESEARCH",),
    )
    text = discovery_text(first)
    assert text == discovery_text(equivalent)
    assert metadata_hash(first) == metadata_hash(equivalent)
    assert text.startswith("capability: Search technical docs\ncapability: Compare technologies\n")
    assert "name: Research Agent" in text
    assert "description: Searches technical information" in text
    assert "tag: research" in text
    assert "tool: web_search" in text
    assert "workflow://" not in text


@pytest.mark.parametrize(
    "change",
    [
        {"name": "Analysis Agent"},
        {"description": "Analyzes technical information"},
        {"capabilities": ("Analyze source code", "Compare technologies")},
        {"tags": ("analysis",)},
        {"tools": ("file_search",)},
    ],
)
def test_each_semantic_field_changes_text_and_hash(change):
    original = _content()
    changed = _content(**change)
    assert discovery_text(changed) != discovery_text(original)
    assert metadata_hash(changed) != metadata_hash(original)


def test_nonsemantic_fields_do_not_enter_embedding_text_or_hash():
    agent = AgentMetadata.register(_content())
    disabled = agent.set_status("disabled")
    revised_ref = _content(runtime_ref="workflow://research-agent/2")
    assert discovery_text(agent.snapshot) == discovery_text(disabled.snapshot)
    assert discovery_text(agent.snapshot) == discovery_text(revised_ref)
    assert metadata_hash(agent.snapshot) == metadata_hash(revised_ref)
    assert str(agent.snapshot.id) not in discovery_text(agent.snapshot)
    assert "fake-v1" not in discovery_text(agent.snapshot)


def test_public_text_rejects_credential_and_runtime_configuration_fields():
    with pytest.raises(ValidationError):
        _content(description="api_key=example-placeholder")
    with pytest.raises(ValidationError):
        _content(role="internal system prompt")
    with pytest.raises(ValidationError):
        _content(provider_config={"endpoint": "internal"})


def test_batch_preserves_snapshot_vector_correspondence():
    first = AgentMetadata.register(_content())
    second = AgentMetadata.register(_content(name="Code Agent"))
    backend = _backend(first.snapshot, second.snapshot)
    records = prepare_embeddings((second.snapshot, first.snapshot), backend)
    assert [(record.agent_id, record.version, record.vector) for record in records] == [
        (second.snapshot.id, 1, (2.0, 1.0)),
        (first.snapshot.id, 1, (1.0, 1.0)),
    ]
    assert all(record.embedding_model_key == "fake-v1" for record in records)
    assert all(record.dimensions == 2 for record in records)
    assert [record.metadata_hash for record in records] == [
        metadata_hash(second.snapshot),
        metadata_hash(first.snapshot),
    ]


def test_invalid_batch_is_rejected_before_any_index_write(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    versions = AgentVersionRepository(database)
    index = AgentEmbeddingRepository(database)
    agent = AgentMetadata.register(_content())
    with database.transaction() as connection:
        versions.insert_initial(connection, agent)

    class BadBackend:
        model_key = "bad-v1"
        dimensions = 2

        def embed(self, texts):
            return [(1.0, 0.0), (float("nan"), 1.0)]

    with pytest.raises(EmbeddingValidationError, match="NON_FINITE_VECTOR"):
        prepare_embeddings((agent.snapshot, agent.snapshot), BadBackend())
    assert index.get(agent.snapshot.id, 1, "bad-v1") is None


def test_bad_record_and_duplicate_roll_back_whole_batch_even_if_caught(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    versions = AgentVersionRepository(database)
    index = AgentEmbeddingRepository(database)
    first = AgentMetadata.register(_content())
    second = AgentMetadata.register(_content(name="Code Agent"))
    records = prepare_embeddings((first.snapshot, second.snapshot), _backend(first.snapshot, second.snapshot))
    with database.transaction() as connection:
        versions.insert_initial(connection, first)
        versions.insert_initial(connection, second)
        with pytest.raises(ValueError, match="hash"):
            index.insert_many(connection, (records[0], replace(records[1], metadata_hash="0" * 64)))
    assert index.get(first.snapshot.id, 1, "fake-v1") is None
    assert index.get(second.snapshot.id, 1, "fake-v1") is None

    with database.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            index.insert_many(connection, (records[0], records[0]))
    assert index.get(first.snapshot.id, 1, "fake-v1") is None


def test_new_version_gets_new_embedding_and_old_version_remains(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    versions = AgentVersionRepository(database)
    index = AgentEmbeddingRepository(database)
    initial = AgentMetadata.register(_content())
    updated = initial.update_content(_content(description="Analyzes technical documents"))
    initial_record, updated_record = prepare_embeddings(
        (initial.snapshot, updated.snapshot), _backend(initial.snapshot, updated.snapshot)
    )
    with database.transaction() as connection:
        versions.insert_initial(connection, initial)
        index.insert_many(connection, (initial_record,))
    with database.transaction() as connection:
        versions.insert_next(connection, updated.snapshot, updated.updated_at)
        index.insert_many(connection, (updated_record,))
    assert index.get(initial.snapshot.id, 1, "fake-v1") == initial_record
    assert index.get(initial.snapshot.id, 2, "fake-v1") == updated_record
    assert updated_record.metadata_hash != initial_record.metadata_hash
    assert updated_record.vector != initial_record.vector


def test_index_write_and_version_switch_roll_back_together(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    versions = AgentVersionRepository(database)
    index = AgentEmbeddingRepository(database)
    agent = AgentMetadata.register(_content())
    initial = prepare_embeddings((agent.snapshot,), _backend(agent.snapshot))[0]
    with database.transaction() as connection:
        versions.insert_initial(connection, agent)
        index.insert_many(connection, (initial,))

    updated = agent.update_content(_content(name="Updated Research Agent"))
    next_record = prepare_embeddings((updated.snapshot,), _backend(updated.snapshot))[0]
    with pytest.raises(RuntimeError, match="interrupted"):
        with database.transaction() as connection:
            versions.insert_next(connection, updated.snapshot, updated.updated_at)
            index.insert_many(connection, (next_record,))
            raise RuntimeError("interrupted")

    assert versions.get_current(agent.snapshot.id) == agent
    assert versions.get_version(agent.snapshot.id, 2) is None
    assert index.get(agent.snapshot.id, 1, "fake-v1") == initial
    assert index.get(agent.snapshot.id, 2, "fake-v1") is None


def test_reopen_keeps_version_model_hash_dimensions_and_vector(tmp_path):
    path = tmp_path / "agenthub.db"
    database = AgentHubDatabase(path)
    versions = AgentVersionRepository(database)
    index = AgentEmbeddingRepository(database)
    agent = AgentMetadata.register(_content())
    record = prepare_embeddings((agent.snapshot,), _backend(agent.snapshot))[0]
    with database.transaction() as connection:
        versions.insert_initial(connection, agent)
        index.insert_many(connection, (record,))

    reopened = AgentEmbeddingRepository(AgentHubDatabase(path))
    assert reopened.get(agent.snapshot.id, 1, "fake-v1") == record
    assert reopened.get(agent.snapshot.id, 2, "fake-v1") is None
    assert reopened.get(agent.snapshot.id, 1, "other-model") is None


def test_index_writes_require_transaction_and_valid_version(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    AgentVersionRepository(database)
    index = AgentEmbeddingRepository(database)
    agent = AgentMetadata.register(_content())
    record = prepare_embeddings((agent.snapshot,), _backend(agent.snapshot))[0]
    with database.connection() as connection:
        with pytest.raises(RuntimeError, match="transaction"):
            index.insert_many(connection, (record,))
    with pytest.raises(ValueError, match="version does not exist"):
        with database.transaction() as connection:
            index.insert_many(connection, (record,))
    assert index.get(agent.snapshot.id, 1, "fake-v1") is None
