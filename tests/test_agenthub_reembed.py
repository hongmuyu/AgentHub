"""Controlled full-catalog re-embedding and active index switch."""

import sqlite3

import pytest

from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.discovery import AgentDiscovery, discovery_text, prepare_embeddings
from server.services.agenthub.embeddings import FakeEmbeddingBackend
from server.services.agenthub.metadata import AgentMetadata, AgentMetadataInput
from server.services.agenthub.reembed import AgentIndexRebuilder, IndexSnapshotChangedError
from server.services.agenthub.registry import AgentRegistry
from server.services.agenthub.embedding_index import (
    ActiveIndexMismatchError,
    AgentEmbeddingRepository,
)
from server.services.agenthub.versions import AgentVersionRepository


TASK = "choose an agent"


class TrustedValidator:
    def validate(self, runtime_ref):
        return None


def content(name):
    return AgentMetadataInput(
        name=name,
        description=f"{name} handles tasks",
        capabilities=(f"{name} capability",),
        tags=("public",),
        runtime_ref="workflow://fixture/1",
    )


def backend(model_key, dimensions, vectors):
    return FakeEmbeddingBackend(vectors, model_key=model_key, dimensions=dimensions)


def catalog(tmp_path, *, include_update=False):
    first, second = content("Research"), content("Code")
    updated = content("Research Updated")
    old_vectors = {
        discovery_text(first): (1.0, 0.0),
        discovery_text(second): (0.0, 1.0),
        TASK: (1.0, 0.0),
    }
    if include_update:
        old_vectors[discovery_text(updated)] = (0.5, 0.5)
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    old = backend("old-space", 2, old_vectors)
    registry = AgentRegistry(database, TrustedValidator(), old)
    agents = (registry.register(first), registry.register(second))
    new = backend("new-space", 3, {
        discovery_text(first): (0.0, 1.0, 0.0),
        discovery_text(second): (1.0, 0.0, 0.0),
        TASK: (1.0, 0.0, 0.0),
    })
    return registry, agents, new, updated


def discovery(registry, embedding_backend):
    return AgentDiscovery(registry.versions, registry.index, registry.validator, embedding_backend)


def test_complete_rebuild_switches_only_after_all_versions_are_ready(tmp_path):
    registry, (first, second), new, _ = catalog(tmp_path)
    registry.disable(second.snapshot.id)
    assert registry.index.get_active() == ("old-space", 2)
    assert [item.agent_id for item in discovery(registry, registry.backend).discover(TASK, k=2)] == [
        first.snapshot.id
    ]

    AgentIndexRebuilder(registry.database).rebuild(new)

    assert registry.index.get_active() == ("new-space", 3)
    reopened = AgentEmbeddingRepository(AgentHubDatabase(registry.database.path))
    assert reopened.get_active() == ("new-space", 3)
    for agent in (first, second):
        assert registry.index.get(agent.snapshot.id, 1, "old-space") is not None
        record = registry.index.get(agent.snapshot.id, 1, "new-space")
        assert record is not None
        assert record.dimensions == 3
    assert [item.agent_id for item in discovery(registry, new).discover(TASK, k=2)] == [
        first.snapshot.id
    ]
    with pytest.raises(ActiveIndexMismatchError):
        discovery(registry, registry.backend).discover(TASK, k=2)

    registry.backend = new
    registry.enable(second.snapshot.id)
    assert [item.agent_id for item in discovery(registry, new).discover(TASK, k=2)] == [
        second.snapshot.id, first.snapshot.id
    ]


def test_partial_embedding_failure_keeps_old_index_and_no_new_records(tmp_path):
    registry, (first, second), _, _ = catalog(tmp_path)
    incomplete = backend("new-space", 3, {discovery_text(first.snapshot): (1, 0, 0)})

    with pytest.raises(KeyError):
        AgentIndexRebuilder(registry.database).rebuild(incomplete)

    assert registry.index.get_active() == ("old-space", 2)
    assert registry.index.get(first.snapshot.id, 1, "new-space") is None
    assert registry.index.get(second.snapshot.id, 1, "new-space") is None
    assert len(discovery(registry, registry.backend).discover(TASK, k=2)) == 2


def test_switch_failure_rolls_back_every_new_vector(tmp_path):
    registry, (first, second), new, _ = catalog(tmp_path)
    with registry.database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER reject_active_switch BEFORE UPDATE ON agenthub_active_embedding_index
               BEGIN SELECT RAISE(ABORT, 'switch failure'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="switch failure"):
        AgentIndexRebuilder(registry.database).rebuild(new)

    assert registry.index.get_active() == ("old-space", 2)
    assert registry.index.get(first.snapshot.id, 1, "new-space") is None
    assert registry.index.get(second.snapshot.id, 1, "new-space") is None
    assert len(discovery(registry, registry.backend).discover(TASK, k=2)) == 2


def test_metadata_update_during_build_aborts_without_publishing_stale_index(tmp_path):
    registry, (first, second), new, updated = catalog(tmp_path, include_update=True)

    class UpdatingBackend:
        model_key = new.model_key
        dimensions = new.dimensions

        def embed(self, texts):
            registry.update(first.snapshot.id, updated)
            return new.embed(texts)

    with pytest.raises(IndexSnapshotChangedError):
        AgentIndexRebuilder(registry.database).rebuild(UpdatingBackend())

    assert registry.index.get_active() == ("old-space", 2)
    assert registry.get(first.snapshot.id).snapshot.version == 2
    assert registry.get(second.snapshot.id).snapshot.version == 1
    assert registry.index.get(first.snapshot.id, 1, "new-space") is None
    assert registry.index.get(second.snapshot.id, 1, "new-space") is None
    assert len(discovery(registry, registry.backend).discover(TASK, k=2)) == 2


def test_new_agent_during_build_aborts_whole_directory_switch(tmp_path):
    registry, (first, second), new, _ = catalog(tmp_path)
    third = content("Data")
    registry.backend = backend("old-space", 2, {
        discovery_text(third): (1, 1), TASK: (1, 0)
    })

    class RegisteringBackend:
        model_key = new.model_key
        dimensions = new.dimensions

        def embed(self, texts):
            registry.register(third)
            return new.embed(texts)

    with pytest.raises(IndexSnapshotChangedError):
        AgentIndexRebuilder(registry.database).rebuild(RegisteringBackend())

    assert registry.index.get_active() == ("old-space", 2)
    assert len(registry.list()) == 3
    for agent in (first, second):
        assert registry.index.get(agent.snapshot.id, 1, "new-space") is None


def test_old_backend_cannot_publish_new_current_version_after_switch(tmp_path):
    registry, (first, _), new, updated = catalog(tmp_path, include_update=True)
    AgentIndexRebuilder(registry.database).rebuild(new)

    with pytest.raises(ActiveIndexMismatchError):
        registry.update(first.snapshot.id, updated)

    assert registry.get(first.snapshot.id).snapshot.version == 1
    assert registry.index.get(first.snapshot.id, 2, "old-space") is None
    assert registry.index.get_active() == ("new-space", 3)


def test_dimension_change_cannot_reuse_active_model_key(tmp_path):
    registry, (first, second), new, _ = catalog(tmp_path)
    reused_key = backend("old-space", 3, {
        discovery_text(first.snapshot): (1, 0, 0),
        discovery_text(second.snapshot): (0, 1, 0),
    })

    with pytest.raises(ValueError, match="model key must differ"):
        AgentIndexRebuilder(registry.database).rebuild(reused_key)

    assert registry.index.get_active() == ("old-space", 2)
    assert registry.index.get(first.snapshot.id, 1, "old-space").dimensions == 2
    assert registry.index.get(second.snapshot.id, 1, "old-space").dimensions == 2


def test_legacy_directory_without_pointer_cannot_mix_models_on_update(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    versions = AgentVersionRepository(database)
    index = AgentEmbeddingRepository(database)
    initial, revised = content("Legacy"), content("Legacy Updated")
    agent = AgentMetadata.register(initial)
    old = backend("old-space", 2, {
        discovery_text(initial): (1, 0),
        discovery_text(revised): (0, 1),
    })
    with database.transaction() as connection:
        versions.insert_initial(connection, agent)
        index.insert_many(connection, prepare_embeddings((agent.snapshot,), old))
    assert index.get_active() is None

    wrong = AgentRegistry(database, TrustedValidator(), backend(
        "new-space", 3, {discovery_text(revised): (1, 0, 0)}
    ))
    with pytest.raises(ActiveIndexMismatchError):
        wrong.update(agent.snapshot.id, revised)
    assert index.get_active() is None
    assert versions.get_current(agent.snapshot.id).snapshot.version == 1

    correct = AgentRegistry(database, TrustedValidator(), old)
    correct.update(agent.snapshot.id, revised)
    assert index.get_active() == ("old-space", 2)
    assert versions.get_current(agent.snapshot.id).snapshot.version == 2
