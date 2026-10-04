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
from server.services.agenthub.registry import AgentRegistry
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
