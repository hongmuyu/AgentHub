"""The six demo Agents are ordinary, repeatable catalog fixtures."""

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from entity.messages import Message, MessageRole
from runtime.node.agent import ModelResponse
from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.demo_catalog import DemoCatalogConflictError, register_demo_catalog
from server.services.agenthub.discovery import AgentDiscovery, discovery_text
from server.services.agenthub.embeddings import FakeEmbeddingBackend
from server.services.agenthub.metadata import AgentMetadataInput
from server.services.agenthub.registry import AgentRegistry
from server.services.agenthub.router import CalibratedThreshold, SemanticRouter
from server.services.agenthub.runtime_resolver import RuntimeRefResolver
from server.services.agenthub.thin_workflow import ThinWorkflowValidator
from server.services.attachment_service import AttachmentService
from server.services.session_execution import SessionExecutionController
from server.services.session_store import SessionStatus, WorkflowSessionStore
from server.services.websocket_executor import WebSocketGraphExecutor
from server.services.workflow_run_service import WorkflowRunService


ROOT = Path(__file__).resolve().parents[1] / "yaml_instance"
STEMS = ("research", "code", "data", "document", "planning", "review")
MODEL_KEY = "catalog-fixture"


@pytest.fixture(autouse=True)
def fixture_provider_settings(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "fixture-model")
    monkeypatch.setenv("BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("API_KEY", "fixture-only")


def content(root, stem):
    return AgentMetadataInput.model_validate_json(
        (root / f"agenthub_{stem}_metadata.json").read_text(encoding="utf-8")
    )


def fixture_backend(root):
    vectors = {
        discovery_text(item): (0.0, float(index + 1))
        for index, item in enumerate(content(root, stem) for stem in STEMS)
    }
    return FakeEmbeddingBackend(vectors, model_key=MODEL_KEY, dimensions=2)


@pytest.mark.parametrize("stem", STEMS)
def test_each_fixture_has_public_boundary_and_valid_single_agent_workflow(stem):
    metadata = content(ROOT, stem)
    workflow = ThinWorkflowValidator(RuntimeRefResolver(ROOT)).validate(metadata.runtime_ref)
    manifest = json.loads((ROOT / "agenthub_manifest.json").read_text(encoding="utf-8"))
    graph = yaml.safe_load(workflow.path.read_text(encoding="utf-8"))["graph"]

    assert metadata.name == f"{stem.title()} Agent"
    assert len(metadata.capabilities) >= 3
    assert metadata.description and "does not" in metadata.description.lower()
    assert metadata.tags and "knowledge-work" in metadata.tags
    assert manifest[metadata.runtime_ref] == workflow.path.name
    assert workflow.revision == 1
    assert graph["start"] == graph["end"] == [workflow.agent_node_id]
    assert graph["edges"] == []
    assert [(node["id"], node["type"]) for node in graph["nodes"]] == [
        (workflow.agent_node_id, "agent")
    ]
    tooling = graph["nodes"][0]["config"]["tooling"]
    names = [tool["name"] for block in tooling for tool in block["config"]["tools"]]
    assert tuple(names) == metadata.tools


@pytest.mark.parametrize("stem", STEMS)
def test_each_fixture_maps_direct_task_to_mock_final_output(stem, tmp_path, monkeypatch):
    metadata = content(ROOT, stem)
    workflow = ThinWorkflowValidator(RuntimeRefResolver(ROOT)).validate(metadata.runtime_ref)
    store = WorkflowSessionStore()
    service = WorkflowRunService(
        store, SessionExecutionController(store), AttachmentService(root=tmp_path / "attachments"),
    )
    monkeypatch.setattr("server.services.workflow_run_service.YAML_DIR", ROOT)
    monkeypatch.setattr("server.services.workflow_run_service.WARE_HOUSE_DIR", tmp_path / "results")
    observed = {}
    output = f"Mock result for {stem}"

    class MockProvider:
        def __init__(self, config):
            self.config = config

        def create_client(self):
            return object()

        def call_model(self, client, **kwargs):
            observed["conversation"] = kwargs["conversation"]
            observed["tools"] = kwargs["tool_specs"]
            return ModelResponse(message=Message(role=MessageRole.ASSISTANT, content=output))

    monkeypatch.setattr(
        "runtime.node.executor.agent_executor.ProviderRegistry.get_provider",
        lambda name: MockProvider,
    )
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: pytest.fail("fixture mock must not access network"),
    )
    tool_execution = AsyncMock(side_effect=AssertionError("external tool call is not authorized"))
    monkeypatch.setattr(
        "runtime.node.agent.tool.tool_manager.ToolManager.execute_tool", tool_execution,
    )
    original_execute = WebSocketGraphExecutor.execute_graph_async

    async def capture_executor(self, task_input):
        await original_execute(self, task_input)
        observed["executor"] = self

    monkeypatch.setattr(WebSocketGraphExecutor, "execute_graph_async", capture_executor)

    class Sink:
        def __init__(self):
            self.events = []

        async def send_message(self, session_id, message):
            self.events.append(message)

        def send_message_sync(self, session_id, message):
            self.events.append(message)

    sink = Sink()
    task = f"Review supplied {stem} material for an internal project."
    asyncio.run(service.start_workflow(f"{stem}-fixture", workflow.path.name, task, sink))

    assert store.get_session(f"{stem}-fixture").status == SessionStatus.COMPLETED
    assert observed["executor"].get_final_output() == output
    assert any(
        message.role == MessageRole.USER and message.text_content() == task
        for message in observed["conversation"]
    )
    assert {tool.name for tool in (observed["tools"] or ())} == set(metadata.tools)
    assert {event["type"] for event in sink.events} >= {"workflow_started", "workflow_completed"}
    tool_execution.assert_not_awaited()


def test_catalog_registration_is_repeatable_with_persisted_uuid_version_mapping(tmp_path):
    database = AgentHubDatabase(tmp_path / "catalog.db")
    validator = ThinWorkflowValidator(RuntimeRefResolver(ROOT))
    fake = fixture_backend(ROOT)

    class CountingBackend:
        model_key = fake.model_key
        dimensions = fake.dimensions

        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += 1
            return fake.embed(texts)

    backend = CountingBackend()
    registry = AgentRegistry(database, validator, backend)
    first = register_demo_catalog(registry, ROOT)
    second = register_demo_catalog(registry, ROOT)
    reopened = AgentRegistry(AgentHubDatabase(database.path), validator, backend)
    third = register_demo_catalog(reopened, ROOT)

    assert len(first) == len(STEMS)
    assert set(first) == {content(ROOT, stem).runtime_ref for stem in STEMS}
    assert backend.calls == len(STEMS)
    assert {ref: (agent.snapshot.id, agent.snapshot.version) for ref, agent in first.items()} == {
        ref: (agent.snapshot.id, agent.snapshot.version) for ref, agent in second.items()
    } == {
        ref: (agent.snapshot.id, agent.snapshot.version) for ref, agent in third.items()
    }
    assert all(agent.snapshot.version == 1 for agent in first.values())
    assert len(reopened.list(include_disabled=True, limit=10)) == len(STEMS)


def test_conflicting_existing_identity_is_rejected_before_catalog_writes(tmp_path):
    database = AgentHubDatabase(tmp_path / "catalog.db")
    research = content(ROOT, "research")
    different = research.model_copy(update={"name": "Different Research"})
    vectors = {discovery_text(content(ROOT, stem)): (0.0, 1.0) for stem in STEMS}
    vectors[discovery_text(different)] = (1.0, 0.0)
    registry = AgentRegistry(
        database, ThinWorkflowValidator(RuntimeRefResolver(ROOT)),
        FakeEmbeddingBackend(vectors, model_key=MODEL_KEY, dimensions=2),
    )
    registry.register(different)

    with pytest.raises(DemoCatalogConflictError):
        register_demo_catalog(registry, ROOT)
    assert len(registry.list(include_disabled=True, limit=10)) == 1


def test_seventh_fixture_registers_and_routes_without_router_changes(tmp_path):
    root = tmp_path / "workflows"
    root.mkdir()
    for stem in STEMS:
        for suffix in (".yaml", "_metadata.json"):
            shutil.copy(ROOT / f"agenthub_{stem}{suffix}", root)
    manifest = json.loads((ROOT / "agenthub_manifest.json").read_text(encoding="utf-8"))
    seventh_ref = "workflow://security-agent/1"
    manifest[seventh_ref] = "agenthub_security.yaml"
    (root / "agenthub_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    workflow = yaml.safe_load((ROOT / "agenthub_planning.yaml").read_text(encoding="utf-8"))
    workflow["graph"]["id"] = "agenthub_security"
    (root / "agenthub_security.yaml").write_text(yaml.safe_dump(workflow), encoding="utf-8")
    seventh = AgentMetadataInput(
        name="Security Agent",
        description="Reviews supplied threat models; does not scan systems or access private assets.",
        capabilities=("Review supplied threat models", "Identify design risks"),
        tags=("security", "knowledge-work"),
        runtime_ref=seventh_ref,
    )
    (root / "agenthub_security_metadata.json").write_text(
        seventh.model_dump_json(), encoding="utf-8"
    )
    task = "Review this supplied threat model"
    vectors = {discovery_text(content(root, stem)): (0.0, 1.0) for stem in STEMS}
    vectors[discovery_text(seventh)] = (1.0, 0.0)
    vectors[task] = (1.0, 0.0)
    backend = FakeEmbeddingBackend(vectors, model_key=MODEL_KEY, dimensions=2)
    registry = AgentRegistry(
        AgentHubDatabase(tmp_path / "catalog.db"),
        ThinWorkflowValidator(RuntimeRefResolver(root)), backend,
    )

    mapping = register_demo_catalog(registry, root)
    decision = SemanticRouter(
        AgentDiscovery(registry.versions, registry.index, registry.validator, backend),
        threshold=CalibratedThreshold(value=0.5, source="fixture-only"),
        top_k=7,
    ).route(task)

    assert len(mapping) == 7
    assert decision.status == "selected"
    assert decision.selected_agent.agent_id == mapping[seventh_ref].snapshot.id
    assert decision.selected_agent.version == 1
