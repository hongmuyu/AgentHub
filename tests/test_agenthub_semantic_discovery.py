"""Exact semantic Top-K over the current eligible Agent catalog."""

import json
from datetime import datetime, timezone
from uuid import UUID

import pytest
import yaml

from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.discovery import AgentDiscovery, discovery_text, prepare_embeddings
from server.services.agenthub.embeddings import FakeEmbeddingBackend
from server.services.agenthub.metadata import AgentMetadata, AgentMetadataInput, AgentMetadataVersion
from server.services.agenthub.registry import AgentRegistry
from server.services.agenthub.runtime_resolver import RuntimeRefResolver, RuntimeReferenceError
from server.services.agenthub.thin_workflow import ThinWorkflowValidator


TASK = "find a suitable agent"


def _catalog(tmp_path, rows, *, query_vector=(1.0, 0.0), include_task=True):
    root = tmp_path / "workflows"
    root.mkdir()
    workflow = root / "thin.yaml"
    workflow.write_text(
        yaml.safe_dump({
            "version": "0.4.0",
            "vars": {},
            "graph": {
                "id": "thin_agent",
                "start": ["agent"],
                "end": ["agent"],
                "nodes": [{
                    "id": "agent", "type": "agent",
                    "config": {"provider": "openai", "name": "fixture-model", "role": "Act."},
                }],
                "edges": [],
            },
        }),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    contents = []
    vectors = {}
    manifest = {}
    for number, name, vector, status in rows:
        reference = f"workflow://agent-{number}/1"
        content = AgentMetadataInput(
            name=name,
            description=f"{name} handles tasks",
            capabilities=(f"{name} capability",),
            tags=("public",),
            runtime_ref=reference,
        )
        contents.append((number, status, content))
        vectors[discovery_text(content)] = vector
        manifest[reference] = workflow.name
    if include_task:
        vectors[TASK] = query_vector
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    backend = FakeEmbeddingBackend(vectors, model_key="fake-v1", dimensions=2)
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    validator = ThinWorkflowValidator(RuntimeRefResolver(root, manifest_path))
    registry = AgentRegistry(database, validator, backend)
    agents = {}
    now = datetime.now(timezone.utc)
    with database.transaction() as connection:
        for number, status, content in contents:
            agent_id = UUID(int=number)
            snapshot = AgentMetadataVersion(**content.model_dump(), id=agent_id, version=1)
            agent = AgentMetadata(snapshot=snapshot, status=status, created_at=now, updated_at=now)
            registry.versions.insert_initial(connection, agent)
            registry.index.insert_many(
                connection, prepare_embeddings((snapshot,), backend)
            )
            agents[number] = agent
    discovery = AgentDiscovery(registry.versions, registry.index, validator, backend)
    return discovery, registry, agents, manifest_path


def test_exact_cosine_orders_positive_zero_and_negative_scores(tmp_path):
    discovery, _, agents, _ = _catalog(tmp_path, [
        (1, "Direct", (1.0, 0.0), "active"),
        (2, "Diagonal", (1.0, 1.0), "active"),
        (3, "Orthogonal", (0.0, 1.0), "active"),
        (4, "Opposite", (-1.0, 0.0), "active"),
    ])

    candidates = discovery.discover(TASK, k=4)

    assert [candidate.agent_id for candidate in candidates] == [
        agents[number].snapshot.id for number in (1, 2, 3, 4)
    ]
    assert [candidate.version for candidate in candidates] == [1, 1, 1, 1]
    assert [candidate.raw_similarity for candidate in candidates] == pytest.approx(
        [1.0, 2 ** -0.5, 0.0, -1.0]
    )
    assert candidates[0].public_metadata.name == "Direct"
    assert candidates[0].public_metadata.capabilities == ("Direct capability",)
    assert not hasattr(candidates[0].public_metadata, "runtime_ref")
    assert "fixture-model" not in str(candidates[0].public_metadata)


@pytest.mark.parametrize("k,expected", [(1, (1,)), (2, (1, 2)), (3, (1, 2)), (10, (1, 2))])
def test_top_k_returns_only_available_candidates(tmp_path, k, expected):
    discovery, _, agents, _ = _catalog(tmp_path, [
        (1, "First", (1.0, 0.0), "active"),
        (2, "Second", (0.0, 1.0), "active"),
    ])
    assert tuple(candidate.agent_id for candidate in discovery.discover(TASK, k=k)) == tuple(
        agents[number].snapshot.id for number in expected
    )


@pytest.mark.parametrize("rows", [[], [(1, "Disabled", (1.0, 0.0), "disabled")]])
def test_empty_or_ineligible_catalog_does_not_embed_query(tmp_path, rows):
    discovery, _, _, _ = _catalog(tmp_path, rows, include_task=False)
    assert discovery.discover(TASK, k=3) == ()


@pytest.mark.parametrize("k", [0, -1, True, 1.5])
def test_invalid_k_is_rejected(tmp_path, k):
    discovery, _, _, _ = _catalog(tmp_path, [])
    with pytest.raises(ValueError, match="k"):
        discovery.discover(TASK, k=k)


def test_tied_scores_use_id_order_even_when_input_order_reverses(tmp_path):
    discovery, registry, agents, _ = _catalog(tmp_path, [
        (3, "Third", (1.0, 0.0), "active"),
        (1, "First", (1.0, 0.0), "active"),
        (2, "Second", (1.0, 0.0), "active"),
    ])

    class ReverseCurrent:
        def list_current(self, status=None):
            return tuple(reversed(registry.versions.list_current(status)))

    reversed_discovery = AgentDiscovery(
        ReverseCurrent(), registry.index, registry.validator, registry.backend
    )
    expected = [agents[number].snapshot.id for number in (1, 2, 3)]
    assert [item.agent_id for item in discovery.discover(TASK, k=3)] == expected
    assert [item.agent_id for item in reversed_discovery.discover(TASK, k=3)] == expected


def test_disabled_and_invalid_reference_are_excluded(tmp_path):
    discovery, registry, agents, manifest_path = _catalog(tmp_path, [
        (1, "Available", (0.0, 1.0), "active"),
        (2, "Disabled", (1.0, 0.0), "disabled"),
        (3, "Unlisted", (1.0, 0.0), "active"),
    ])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest[agents[3].snapshot.runtime_ref]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert [item.agent_id for item in discovery.discover(TASK, k=3)] == [
        agents[1].snapshot.id
    ]
    assert registry.get(agents[2].snapshot.id).status == "disabled"


def test_missing_mixed_model_dimension_hash_and_bad_vector_are_excluded(tmp_path):
    discovery, registry, agents, _ = _catalog(tmp_path, [
        (1, "Valid", (0.0, 1.0), "active"),
        (2, "OtherModel", (1.0, 0.0), "active"),
        (3, "OtherDimensions", (1.0, 0.0), "active"),
        (4, "WrongHash", (1.0, 0.0), "active"),
        (5, "BadVector", (1.0, 0.0), "active"),
        (6, "Missing", (1.0, 0.0), "active"),
    ])
    with registry.database.transaction() as connection:
        connection.execute(
            "UPDATE agenthub_agent_embeddings SET embedding_model_key = 'other-model' "
            "WHERE agent_id = ?", (str(agents[2].snapshot.id),)
        )
        connection.execute(
            "UPDATE agenthub_agent_embeddings SET dimensions = 3, vector_json = '[1,0,0]' "
            "WHERE agent_id = ?", (str(agents[3].snapshot.id),)
        )
        connection.execute(
            "UPDATE agenthub_agent_embeddings SET metadata_hash = ? WHERE agent_id = ?",
            ("0" * 64, str(agents[4].snapshot.id)),
        )
        connection.execute(
            "UPDATE agenthub_agent_embeddings SET vector_json = '[0,0]' WHERE agent_id = ?",
            (str(agents[5].snapshot.id),)
        )
        connection.execute(
            "DELETE FROM agenthub_agent_embeddings WHERE agent_id = ?",
            (str(agents[6].snapshot.id),)
        )

    assert [item.agent_id for item in discovery.discover(TASK, k=6)] == [
        agents[1].snapshot.id
    ]


def test_status_toggle_changes_new_discovery_queries(tmp_path):
    discovery, registry, agents, _ = _catalog(tmp_path, [
        (1, "First", (1.0, 0.0), "active"),
        (2, "Second", (0.0, 1.0), "active"),
    ])
    first_id, second_id = agents[1].snapshot.id, agents[2].snapshot.id
    assert [item.agent_id for item in discovery.discover(TASK, k=2)] == [first_id, second_id]
    registry.disable(first_id)
    assert [item.agent_id for item in discovery.discover(TASK, k=2)] == [second_id]
    registry.enable(first_id)
    assert [item.agent_id for item in discovery.discover(TASK, k=2)] == [first_id, second_id]


def test_new_query_uses_only_current_version(tmp_path):
    discovery, registry, agents, _ = _catalog(tmp_path, [
        (1, "Versioned", (1.0, 0.0), "active"),
    ])
    current = agents[1]
    next_snapshot = current.snapshot.model_copy(update={"version": 2})
    with registry.database.transaction() as connection:
        registry.versions.insert_next_snapshot(connection, next_snapshot, current.updated_at)
        registry.index.insert_many(
            connection, prepare_embeddings((next_snapshot,), registry.backend)
        )
        registry.versions.switch_current(connection, next_snapshot, current.updated_at)

    candidates = discovery.discover(TASK, k=2)
    assert [(item.agent_id, item.version) for item in candidates] == [
        (current.snapshot.id, 2)
    ]


def test_query_embedding_and_manifest_failures_propagate(tmp_path):
    discovery, registry, _, manifest_path = _catalog(
        tmp_path, [(1, "Available", (1.0, 0.0), "active")], include_task=False
    )
    with pytest.raises(KeyError, match="not configured"):
        discovery.discover(TASK, k=1)

    manifest_path.write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeReferenceError) as error:
        discovery.discover(TASK, k=1)
    assert error.value.code == "INVALID_WORKFLOW_MANIFEST"
    assert registry.get(UUID(int=1)) is not None
