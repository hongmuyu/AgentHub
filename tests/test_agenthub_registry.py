"""Registry registration and administrative directory reads."""

import json
import sqlite3
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError

from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.discovery import discovery_text, metadata_hash, prepare_embeddings
from server.services.agenthub.embedding_index import AgentEmbeddingRepository
from server.services.agenthub.embeddings import FakeEmbeddingBackend
from server.services.agenthub.metadata import AgentMetadata, AgentMetadataInput
from server.services.agenthub.registry import AgentIndexNotReadyError, AgentRegistry
from server.services.agenthub.runtime_resolver import RuntimeRefResolver, RuntimeReferenceError
from server.services.agenthub.thin_workflow import ThinWorkflowValidator, WorkflowValidationError
from server.services.agenthub.versions import AgentVersionRepository


REFERENCE = "workflow://research-agent/1"


def _content(name="Research Agent", **changes):
    fields = dict(
        name=name,
        description="Summarizes technical reports",
        capabilities=("Search technical docs",),
        tools=("web_search",),
        tags=("research",),
        runtime_ref=REFERENCE,
    )
    fields.update(changes)
    return AgentMetadataInput(**fields)


def _validator(tmp_path, *, valid=True):
    root = tmp_path / "workflows"
    root.mkdir()
    workflow = root / "research.yaml"
    graph = {
        "id": "thin_research",
        "start": ["agent"],
        "end": ["agent"],
        "nodes": [
            {
                "id": "agent",
                "type": "agent" if valid else "passthrough",
                "config": {"provider": "openai", "name": "fixture-model", "role": "Summarize."}
                if valid else {"only_last_message": True},
            }
        ],
        "edges": [],
    }
    workflow.write_text(
        yaml.safe_dump({"version": "0.4.0", "vars": {}, "graph": graph}), encoding="utf-8"
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({REFERENCE: workflow.name}), encoding="utf-8")
    return ThinWorkflowValidator(RuntimeRefResolver(root, manifest))


def _backend(*contents):
    return FakeEmbeddingBackend(
        {discovery_text(content): (float(index + 1), 1.0)
         for index, content in enumerate(contents)},
        model_key="fake-v1",
        dimensions=2,
    )


def _registry(tmp_path, *contents, valid_workflow=True):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    validator = _validator(tmp_path, valid=valid_workflow)
    return AgentRegistry(database, validator, _backend(*contents)), database


def test_register_persists_active_version_and_matching_embedding_across_reopen(tmp_path):
    content = _content()
    registry, database = _registry(tmp_path, content)

    agent = registry.register(content.model_dump())

    assert agent.status == "active"
    assert agent.snapshot.version == 1
    assert registry.get(agent.snapshot.id) == agent
    stored = AgentEmbeddingRepository(database).get(agent.snapshot.id, 1, "fake-v1")
    assert stored is not None
    assert stored.dimensions == 2
    assert stored.vector == (1.0, 1.0)
    assert stored.agent_id == agent.snapshot.id
    assert stored.embedding_model_key == "fake-v1"
    assert stored.metadata_hash == metadata_hash(content)
    assert "fixture-model" not in agent.model_dump_json()
    assert "Summarize." not in agent.model_dump_json()
    reopened = AgentRegistry(AgentHubDatabase(database.path), registry.validator, _backend(content))
    assert reopened.get(agent.snapshot.id) == agent
    assert reopened.list() == (agent,)


def test_invalid_metadata_or_workflow_has_no_visible_registration(tmp_path):
    content = _content()
    registry, database = _registry(tmp_path, content)
    with pytest.raises(ValidationError):
        registry.register({**content.model_dump(), "provider_config": {"api_key": "placeholder"}})
    with pytest.raises(RuntimeReferenceError):
        registry.register(_content(runtime_ref="workflow://unknown/1"))
    assert registry.list(include_disabled=True) == ()

    invalid_root = tmp_path / "invalid"
    invalid_root.mkdir()
    bad_registry, bad_database = _registry(invalid_root, content, valid_workflow=False)
    with pytest.raises(WorkflowValidationError):
        bad_registry.register(content)
    assert bad_registry.list(include_disabled=True) == ()
    with bad_database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM agenthub_agent_embeddings").fetchone()[0] == 0
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM agenthub_agents").fetchone()[0] == 0


def test_embedding_failure_and_index_insert_failure_leave_no_half_agent(tmp_path):
    content = _content()
    registry, database = _registry(tmp_path)
    with pytest.raises(KeyError):
        registry.register(content)
    assert registry.list(include_disabled=True) == ()

    registry.backend = _backend(content)
    with database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER reject_registry_index BEFORE INSERT ON agenthub_agent_embeddings
               BEGIN SELECT RAISE(ABORT, 'index failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="index failure"):
        registry.register(content)
    assert registry.list(include_disabled=True) == ()
    with database.connection() as connection:
        assert connection.execute("SELECT count(*) FROM agenthub_agents").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM agenthub_agent_versions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM agenthub_agent_embeddings").fetchone()[0] == 0


def test_get_unknown_id_and_empty_directory(tmp_path):
    registry, _ = _registry(tmp_path)
    assert registry.get(uuid4()) is None
    assert registry.list() == ()
    assert registry.search("research") == ()


def test_list_paginates_stably_and_filters_disabled_or_status(tmp_path):
    first_content = _content("Research Agent")
    second_content = _content("Code Agent", capabilities=("Review source code",))
    disabled_content = _content("Data Agent", capabilities=("Analyze data",))
    registry, database = _registry(tmp_path, first_content, second_content, disabled_content)
    first = registry.register(first_content)
    second = registry.register(second_content)
    disabled = AgentMetadata.register(disabled_content).set_status("disabled")
    versions = AgentVersionRepository(database)
    index = AgentEmbeddingRepository(database)
    record = prepare_embeddings((disabled.snapshot,), registry.backend)[0]
    with database.transaction() as connection:
        versions.insert_initial(connection, disabled)
        index.insert_many(connection, (record,))

    active = tuple(sorted((first, second), key=lambda agent: str(agent.snapshot.id)))
    all_agents = tuple(sorted((first, second, disabled), key=lambda agent: str(agent.snapshot.id)))
    assert registry.list() == active
    assert registry.list(limit=1, offset=0) == active[:1]
    assert registry.list(limit=1, offset=1) == active[1:]
    assert registry.list(limit=1, offset=2) == ()
    assert registry.list(include_disabled=True) == all_agents
    assert registry.list(status="disabled") == (disabled,)
    assert registry.list(status="active", include_disabled=True) == active
    assert registry.search("Analyze data") == ()
    assert registry.search("Analyze data", status="disabled") == (disabled,)


def test_search_uses_public_fields_not_runtime_ref_or_embedding(tmp_path):
    research = _content("Knowledge Agent")
    code = _content("Code Agent", description="Reviews implementations",
                    capabilities=("Review source code",), tools=("file_search",), tags=("code",))
    registry, _ = _registry(tmp_path, research, code)
    first = registry.register(research)
    second = registry.register(code)

    assert registry.search("KNOWLEDGE") == (first,)
    assert registry.search("technical docs") == (first,)
    assert registry.search("web_search") == (first,)
    assert registry.search("code") == (second,)
    assert registry.search("research-agent") == ()
    assert registry.search("fixture-model") == ()
    assert registry.search("capability:") == ()
    assert registry.search("missing") == ()
    ordered = tuple(sorted((first, second), key=lambda agent: str(agent.snapshot.id)))
    assert registry.search("agent", limit=1, offset=0) == ordered[:1]
    assert registry.search("agent", limit=1, offset=1) == ordered[1:]


@pytest.mark.parametrize("limit,offset", [(0, 0), (-1, 0), (1, -1), (True, 0)])
def test_list_rejects_invalid_pagination(tmp_path, limit, offset):
    registry, _ = _registry(tmp_path)
    with pytest.raises(ValueError):
        registry.list(limit=limit, offset=offset)


def test_update_advances_versions_after_indexing_and_preserves_history(tmp_path):
    initial_content = _content()
    reference_only = _content(runtime_ref="workflow://research-agent/2")
    changed_content = _content(
        "Research Plus", capabilities=("Compare technical docs",),
        runtime_ref="workflow://research-agent/2",
    )
    registry, database = _registry(tmp_path, initial_content, changed_content)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({REFERENCE: "research.yaml", reference_only.runtime_ref: "research.yaml"}),
        encoding="utf-8",
    )
    initial = registry.register(initial_content)
    agent_id = initial.snapshot.id
    first_index = registry.index.get(agent_id, 1, "fake-v1")
    assert first_index is not None
    with database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER index_before_current_switch
               BEFORE INSERT ON agenthub_agent_embeddings
               WHEN NEW.version = 2 AND
                    (SELECT current_version FROM agenthub_agents
                     WHERE agent_id = NEW.agent_id) != 1
               BEGIN SELECT RAISE(ABORT, 'current switched before indexing'); END"""
        )

    second = registry.update(agent_id, reference_only.model_dump())
    third = registry.update(agent_id, changed_content)

    assert [agent.snapshot.version for agent in (initial, second, third)] == [1, 2, 3]
    assert all(agent.snapshot.id == agent_id for agent in (initial, second, third))
    assert registry.get(agent_id) == third
    assert registry.versions.get_version(agent_id, 1) == initial.snapshot
    assert registry.versions.get_version(agent_id, 2) == second.snapshot
    assert registry.versions.get_version(agent_id, 3) == third.snapshot
    assert first_index == registry.index.get(agent_id, 1, "fake-v1")
    second_index = registry.index.get(agent_id, 2, "fake-v1")
    third_index = registry.index.get(agent_id, 3, "fake-v1")
    assert second_index is not None and third_index is not None
    assert second.snapshot.runtime_ref == reference_only.runtime_ref
    assert second_index.metadata_hash == first_index.metadata_hash
    assert second_index.vector == first_index.vector
    assert third_index.metadata_hash != second_index.metadata_hash
    assert third_index.vector != second_index.vector
    reopened = AgentRegistry(AgentHubDatabase(database.path), registry.validator, registry.backend)
    assert reopened.get(agent_id) == third
    assert reopened.versions.get_version(agent_id, 1) == initial.snapshot
    assert reopened.index.get(agent_id, 2, "fake-v1") == second_index


def test_update_keeps_disabled_identity_disabled(tmp_path):
    original = _content()
    changed = _content("Updated Research Agent")
    registry, database = _registry(tmp_path, original, changed)
    disabled = AgentMetadata.register(original).set_status("disabled")
    with database.transaction() as connection:
        registry.versions.insert_initial(connection, disabled)
        registry.index.insert_many(
            connection, prepare_embeddings((disabled.snapshot,), registry.backend)
        )

    updated = registry.update(disabled.snapshot.id, changed)

    assert updated.status == "disabled"
    assert updated.snapshot.version == 2
    assert registry.get(disabled.snapshot.id) == updated
    assert registry.list() == ()
    assert registry.list(status="disabled") == (updated,)
    assert registry.versions.get_version(disabled.snapshot.id, 1) == disabled.snapshot
    assert registry.index.get(disabled.snapshot.id, 2, "fake-v1") is not None


def test_update_validation_or_embedding_failure_keeps_old_version(tmp_path):
    original = _content()
    registry, _ = _registry(tmp_path, original)
    initial = registry.register(original)
    agent_id = initial.snapshot.id
    first_index = registry.index.get(agent_id, 1, "fake-v1")

    with pytest.raises(ValidationError):
        registry.update(agent_id, {**original.model_dump(), "capabilities": ()})
    with pytest.raises(RuntimeReferenceError):
        registry.update(agent_id, _content(runtime_ref="workflow://unknown/1"))
    with pytest.raises(KeyError):
        registry.update(agent_id, _content("Unconfigured Agent"))

    assert registry.get(agent_id) == initial
    assert registry.versions.get_version(agent_id, 2) is None
    assert registry.index.get(agent_id, 1, "fake-v1") == first_index
    assert registry.index.get(agent_id, 2, "fake-v1") is None


def test_update_index_write_failure_rolls_back_version_and_current(tmp_path):
    original = _content()
    changed = _content("Updated Research Agent")
    registry, database = _registry(tmp_path, original, changed)
    initial = registry.register(original)
    agent_id = initial.snapshot.id
    first_index = registry.index.get(agent_id, 1, "fake-v1")
    with database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER reject_next_index BEFORE INSERT ON agenthub_agent_embeddings
               WHEN NEW.version = 2 BEGIN SELECT RAISE(ABORT, 'index failure'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="index failure"):
        registry.update(agent_id, changed)

    assert registry.get(agent_id) == initial
    assert registry.versions.get_version(agent_id, 2) is None
    assert registry.index.get(agent_id, 1, "fake-v1") == first_index
    assert registry.index.get(agent_id, 2, "fake-v1") is None


def test_update_current_switch_failure_rolls_back_new_index(tmp_path):
    original = _content()
    changed = _content("Updated Research Agent")
    registry, database = _registry(tmp_path, original, changed)
    initial = registry.register(original)
    agent_id = initial.snapshot.id
    with database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER reject_current_switch BEFORE UPDATE ON agenthub_agents
               WHEN NEW.current_version = 2
               BEGIN SELECT RAISE(ABORT, 'switch failure'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="switch failure"):
        registry.update(agent_id, changed)

    assert registry.get(agent_id) == initial
    assert registry.versions.get_version(agent_id, 2) is None
    assert registry.index.get(agent_id, 2, "fake-v1") is None


def test_update_unknown_agent_does_not_create_identity(tmp_path):
    content = _content()
    registry, _ = _registry(tmp_path, content)
    unknown_id = uuid4()
    with pytest.raises(LookupError, match="does not exist"):
        registry.update(unknown_id, content)
    assert registry.get(unknown_id) is None
    assert registry.list(include_disabled=True) == ()


def test_disable_enable_are_idempotent_and_preserve_selected_snapshot(tmp_path):
    content = _content()
    registry, database = _registry(tmp_path, content)
    initial = registry.register(content)
    agent_id = initial.snapshot.id
    selected_snapshot = initial.snapshot
    first_index = registry.index.get(agent_id, 1, "fake-v1")
    assert first_index is not None

    disabled = registry.disable(agent_id)
    assert disabled.status == "disabled"
    assert disabled.updated_at > initial.updated_at
    assert disabled.snapshot == selected_snapshot
    assert registry.get(agent_id) == disabled
    assert registry.list() == ()
    assert registry.list(status="disabled") == (disabled,)
    assert registry.disable(agent_id) == disabled
    assert registry.get(agent_id).updated_at == disabled.updated_at

    enabled = registry.enable(agent_id)
    assert enabled.status == "active"
    assert enabled.updated_at > disabled.updated_at
    assert enabled.snapshot == selected_snapshot
    assert registry.enable(agent_id) == enabled
    assert registry.list() == (enabled,)
    assert registry.versions.get_version(agent_id, 1) == selected_snapshot
    assert registry.versions.get_version(agent_id, 2) is None
    assert registry.index.get(agent_id, 1, "fake-v1") == first_index
    assert initial.status == "active"
    reopened = AgentRegistry(AgentHubDatabase(database.path), registry.validator, registry.backend)
    assert reopened.get(agent_id) == enabled


def test_enable_rechecks_manifest_and_preserves_disabled_on_failure(tmp_path):
    content = _content()
    registry, _ = _registry(tmp_path, content)
    agent = registry.register(content)
    disabled = registry.disable(agent.snapshot.id)
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeReferenceError) as error:
        registry.enable(agent.snapshot.id)
    assert error.value.code == "UNKNOWN_RUNTIME_REF"
    assert registry.get(agent.snapshot.id) == disabled
    assert registry.versions.get_version(agent.snapshot.id, 2) is None

    manifest.write_text(json.dumps({REFERENCE: "research.yaml"}), encoding="utf-8")
    assert registry.enable(agent.snapshot.id).status == "active"


def test_enable_rechecks_workflow_validation(tmp_path):
    content = _content()
    registry, _ = _registry(tmp_path, content)
    agent = registry.register(content)
    disabled = registry.disable(agent.snapshot.id)
    (tmp_path / "workflows" / "research.yaml").write_text("graph: [", encoding="utf-8")

    with pytest.raises(WorkflowValidationError):
        registry.enable(agent.snapshot.id)
    assert registry.get(agent.snapshot.id) == disabled


@pytest.mark.parametrize("damage", ["missing", "wrong_hash", "bad_vector"])
def test_enable_rejects_missing_or_invalid_current_index(tmp_path, damage):
    content = _content()
    registry, database = _registry(tmp_path, content)
    agent = registry.register(content)
    disabled = registry.disable(agent.snapshot.id)
    with database.transaction() as connection:
        if damage == "missing":
            connection.execute("DELETE FROM agenthub_agent_embeddings")
        elif damage == "wrong_hash":
            connection.execute(
                "UPDATE agenthub_agent_embeddings SET metadata_hash = ?", ("0" * 64,)
            )
        else:
            connection.execute(
                "UPDATE agenthub_agent_embeddings SET vector_json = ?", ('[0.0, 0.0]',)
            )

    with pytest.raises(AgentIndexNotReadyError) as error:
        registry.enable(agent.snapshot.id)
    assert error.value.code == "INDEX_NOT_READY"
    assert registry.get(agent.snapshot.id) == disabled
    assert registry.versions.get_version(agent.snapshot.id, 2) is None


@pytest.mark.parametrize("model_key,dimensions", [("fake-v2", 2), ("fake-v1", 3)])
def test_enable_requires_current_backend_model_and_dimensions(tmp_path, model_key, dimensions):
    content = _content()
    registry, _ = _registry(tmp_path, content)
    agent = registry.register(content)
    disabled = registry.disable(agent.snapshot.id)
    registry.backend = FakeEmbeddingBackend({}, model_key=model_key, dimensions=dimensions)

    with pytest.raises(AgentIndexNotReadyError):
        registry.enable(agent.snapshot.id)
    assert registry.get(agent.snapshot.id) == disabled


def test_failed_status_write_leaves_identity_and_version_unchanged(tmp_path):
    content = _content()
    registry, database = _registry(tmp_path, content)
    agent = registry.register(content)
    with database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER reject_disable BEFORE UPDATE ON agenthub_agents
               WHEN NEW.status = 'disabled'
               BEGIN SELECT RAISE(ABORT, 'status write failure'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="status write failure"):
        registry.disable(agent.snapshot.id)
    assert registry.get(agent.snapshot.id) == agent
    assert registry.versions.get_version(agent.snapshot.id, 2) is None


def test_enable_disable_unknown_agent_do_not_create_identity(tmp_path):
    registry, _ = _registry(tmp_path)
    agent_id = uuid4()
    with pytest.raises(LookupError, match="does not exist"):
        registry.disable(agent_id)
    with pytest.raises(LookupError, match="does not exist"):
        registry.enable(agent_id)
    assert registry.list(include_disabled=True) == ()
