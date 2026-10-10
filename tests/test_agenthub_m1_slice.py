"""M1 Registry → Discovery → semantic routing, without Agent execution."""

import json
from unittest.mock import patch

import pytest
import yaml

from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.discovery import AgentDiscovery, discovery_text
from server.services.agenthub.embeddings import FakeEmbeddingBackend
from server.services.agenthub.metadata import AgentMetadataInput
from server.services.agenthub.registry import AgentRegistry
from server.services.agenthub.router import CalibratedThreshold, SemanticRouter
from server.services.agenthub.runtime_resolver import RuntimeRefResolver
from server.services.agenthub.thin_workflow import ThinWorkflowValidator


RESEARCH_REF = "workflow://research/1"
DOCUMENT_REF = "workflow://document/1"
OLD_TASK = "Find technical reports"
NEW_TASK = "Compare technical reports"
NO_MATCH_TASK = "Write a marketing slogan"


def _catalog(tmp_path):
    workflow_root = tmp_path / "workflows"
    workflow_root.mkdir()
    manifest = {}
    for reference, filename, graph_id in (
        (RESEARCH_REF, "research.yaml", "thin_research"),
        (DOCUMENT_REF, "document.yaml", "thin_document"),
    ):
        (workflow_root / filename).write_text(
            yaml.safe_dump({
                "version": "0.4.0",
                "vars": {},
                "graph": {
                    "id": graph_id,
                    "start": ["agent"],
                    "end": ["agent"],
                    "nodes": [{
                        "id": "agent",
                        "type": "agent",
                        "config": {
                            "provider": "openai", "name": "fixture-model", "role": "Act."
                        },
                    }],
                    "edges": [],
                },
            }),
            encoding="utf-8",
        )
        manifest[reference] = filename
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    research_v1 = AgentMetadataInput(
        name="Research Agent", description="Finds technical reports",
        capabilities=("search technical reports",), runtime_ref=RESEARCH_REF,
    )
    research_v2 = AgentMetadataInput(
        name="Research Agent", description="Compares technical reports",
        capabilities=("compare technical reports",), runtime_ref=RESEARCH_REF,
    )
    document = AgentMetadataInput(
        name="Document Agent", description="Summarizes documents",
        capabilities=("summarize documents",), runtime_ref=DOCUMENT_REF,
    )
    vectors = {
        discovery_text(research_v1): (1.0, 0.0),
        discovery_text(research_v2): (0.0, 1.0),
        discovery_text(document): (0.6, 0.8),
        OLD_TASK: (1.0, 0.0),
        NEW_TASK: (0.0, 1.0),
        NO_MATCH_TASK: (-1.0, -1.0),
    }

    def open_registry():
        database = AgentHubDatabase(tmp_path / "agenthub.db")
        validator = ThinWorkflowValidator(RuntimeRefResolver(workflow_root, manifest_path))
        backend = FakeEmbeddingBackend(vectors, model_key="m1-fixture", dimensions=2)
        return AgentRegistry(database, validator, backend)

    return open_registry, research_v1, research_v2, document


def _router(registry):
    discovery = AgentDiscovery(
        registry.versions, registry.index, registry.validator, registry.backend
    )
    return SemanticRouter(
        discovery,
        threshold=CalibratedThreshold(value=0.5, source="m1-test-fixture-only"),
        top_k=2,
    )


def test_m1_registry_discovery_router_slice_survives_version_and_status_changes(tmp_path):
    open_registry, research_v1, research_v2, document_content = _catalog(tmp_path)

    with patch(
        "runtime.node.agent.providers.base.ProviderRegistry.get_provider"
    ) as provider_lookup, patch(
        "runtime.node.agent.providers.openai_provider.OpenAIProvider.__init__"
    ) as provider_init, patch(
        "workflow.graph.GraphExecutor.__init__"
    ) as runtime_init, patch(
        "workflow.graph.GraphExecutor.run"
    ) as workflow_run:
        registry = open_registry()
        router = _router(registry)

        empty = router.route(OLD_TASK)
        assert empty.status == "rejected"
        assert empty.reason_code == "NO_SUITABLE_AGENT"
        assert empty.candidates == ()

        research = registry.register(research_v1)
        document = registry.register(document_content)
        research_id, document_id = research.snapshot.id, document.snapshot.id

        selected = router.route(OLD_TASK)
        assert selected.status == "selected"
        assert selected.selected_agent.agent_id == research_id
        assert selected.selected_agent.version == 1
        assert selected.selected_agent.raw_similarity == pytest.approx(1.0)
        assert [item.agent_id for item in selected.candidates] == [research_id, document_id]
        assert not hasattr(selected.selected_agent.public_metadata, "runtime_ref")

        low_score = router.route(NO_MATCH_TASK)
        assert low_score.status == "rejected"
        assert low_score.reason_code == "NO_SUITABLE_AGENT"
        assert len(low_score.candidates) == 2
        assert low_score.candidates[0].raw_similarity < 0.5
        assert low_score.selected_agent is None

        embedding_failure = router.route("unconfigured task")
        assert embedding_failure.status == "failed"
        assert embedding_failure.error_code == "ROUTING_INFRASTRUCTURE_ERROR"
        assert embedding_failure.selected_agent is None

        updated = registry.update(research_id, research_v2)
        assert updated.snapshot.version == 2
        assert registry.versions.get_version(research_id, 1) == research.snapshot
        new_selection = router.route(NEW_TASK)
        assert new_selection.status == "selected"
        assert new_selection.selected_agent.agent_id == research_id
        assert new_selection.selected_agent.version == 2
        assert (
            new_selection.selected_agent.public_metadata.description
            == "Compares technical reports"
        )
        assert new_selection.selected_agent.raw_similarity == pytest.approx(1.0)
        assert router.route(OLD_TASK).selected_agent.agent_id == document_id

        registry.disable(research_id)
        after_disable = router.route(NEW_TASK)
        assert after_disable.status == "selected"
        assert [item.agent_id for item in after_disable.candidates] == [document_id]
        assert after_disable.selected_agent.raw_similarity == pytest.approx(0.8)

        registry.disable(document_id)
        all_disabled = router.route(NEW_TASK)
        assert all_disabled.status == "rejected"
        assert all_disabled.reason_code == "NO_SUITABLE_AGENT"
        assert all_disabled.candidates == ()
        assert registry.list() == ()

        del router, registry
        reopened = open_registry()
        assert reopened.get(research_id).snapshot.version == 2
        assert reopened.get(research_id).status == "disabled"
        assert reopened.get(document_id).status == "disabled"
        assert reopened.versions.get_version(research_id, 1) == research.snapshot
        assert reopened.versions.get_version(research_id, 2) == updated.snapshot
        assert reopened.index.get(research_id, 1, "m1-fixture") is not None
        assert reopened.index.get(research_id, 2, "m1-fixture") is not None
        assert _router(reopened).route(NEW_TASK).candidates == ()

        reopened.enable(research_id)
        after_reopen = _router(reopened).route(NEW_TASK)
        assert after_reopen.status == "selected"
        assert (after_reopen.selected_agent.agent_id, after_reopen.selected_agent.version) == (
            research_id, 2
        )
        assert [item.agent_id for item in after_reopen.candidates] == [research_id]

    provider_lookup.assert_not_called()
    provider_init.assert_not_called()
    runtime_init.assert_not_called()
    workflow_run.assert_not_called()
