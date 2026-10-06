"""The allowlisted Research fixture is an ordinary registered, runnable Agent."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from entity.messages import Message, MessageRole
from runtime.node.agent import ModelResponse
from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.discovery import discovery_text
from server.services.agenthub.embeddings import FakeEmbeddingBackend
from server.services.agenthub.metadata import AgentMetadataInput
from server.services.agenthub.registry import AgentRegistry
from server.services.agenthub.runtime_resolver import RuntimeRefResolver
from server.services.agenthub.thin_workflow import ThinWorkflowValidator
from server.services.attachment_service import AttachmentService
from server.services.session_execution import SessionExecutionController
from server.services.session_store import SessionStatus, WorkflowSessionStore
from server.services.websocket_executor import WebSocketGraphExecutor
from server.services.workflow_run_service import WorkflowRunService


ROOT = Path(__file__).resolve().parents[1] / "yaml_instance"
REFERENCE = "workflow://research-agent/1"
FINAL_OUTPUT = "Finding: the supplied design documents support option A. Source: supplied brief."


@pytest.fixture(autouse=True)
def fixture_provider_settings(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "fixture-model")
    monkeypatch.setenv("BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("API_KEY", "fixture-only")


def _metadata():
    return AgentMetadataInput.model_validate_json(
        (ROOT / "agenthub_research_metadata.json").read_text(encoding="utf-8")
    )


def test_research_fixture_resolves_validates_and_registers_as_ordinary_metadata(tmp_path):
    content = _metadata()
    validator = ThinWorkflowValidator(RuntimeRefResolver(ROOT))
    workflow = validator.validate(content.runtime_ref)
    manifest = json.loads((ROOT / "agenthub_manifest.json").read_text(encoding="utf-8"))
    graph = yaml.safe_load(workflow.path.read_text(encoding="utf-8"))["graph"]
    database = AgentHubDatabase(tmp_path / "research.db")
    backend = FakeEmbeddingBackend(
        {discovery_text(content): (1.0, 0.5)}, model_key="fixture-only", dimensions=2,
    )
    registry = AgentRegistry(database, validator, backend)

    agent = registry.register(content)

    assert content.name == "Research Agent"
    assert content.runtime_ref == REFERENCE
    assert content.tools == ("web_search", "read_webpage_content")
    assert manifest[REFERENCE] == workflow.path.name == "agenthub_research.yaml"
    assert workflow.revision == 1
    assert workflow.graph_id == "agenthub_research"
    assert workflow.agent_node_id == "research"
    assert graph["start"] == graph["end"] == ["research"]
    assert graph["edges"] == []
    assert [(node["id"], node["type"]) for node in graph["nodes"]] == [
        ("research", "agent"),
    ]
    configured_tools = graph["nodes"][0]["config"]["tooling"][0]["config"]["tools"]
    assert [tool["name"] for tool in configured_tools] == list(content.tools)
    assert agent.snapshot.id.int != 0
    assert agent.snapshot.version == 1
    assert agent.snapshot.runtime_ref == REFERENCE
    assert registry.get(agent.snapshot.id) == agent
    assert registry.list() == (agent,)


def test_research_fixture_maps_user_task_to_mock_provider_and_final_output(tmp_path, monkeypatch):
    content = _metadata()
    workflow = ThinWorkflowValidator(RuntimeRefResolver(ROOT)).validate(content.runtime_ref)
    store = WorkflowSessionStore()
    service = WorkflowRunService(
        store, SessionExecutionController(store), AttachmentService(root=tmp_path / "attachments"),
    )
    monkeypatch.setattr("server.services.workflow_run_service.YAML_DIR", ROOT)
    monkeypatch.setattr("server.services.workflow_run_service.WARE_HOUSE_DIR", tmp_path / "results")
    observed = {}

    class MockProvider:
        def __init__(self, config):
            self.config = config

        def create_client(self):
            return object()

        def call_model(self, client, **kwargs):
            observed["conversation"] = kwargs["conversation"]
            observed["tools"] = kwargs["tool_specs"]
            return ModelResponse(message=Message(role=MessageRole.ASSISTANT, content=FINAL_OUTPUT))

    monkeypatch.setattr(
        "runtime.node.executor.agent_executor.ProviderRegistry.get_provider",
        lambda name: MockProvider,
    )

    def forbid_network(*args, **kwargs):
        raise AssertionError("Research fixture mock run must not access the network")

    monkeypatch.setattr("requests.sessions.Session.request", forbid_network)
    tool_execution = AsyncMock(
        side_effect=AssertionError("No external tool is authorized in this test")
    )
    monkeypatch.setattr(
        "runtime.node.agent.tool.tool_manager.ToolManager.execute_tool", tool_execution,
    )
    original_execute = WebSocketGraphExecutor.execute_graph_async

    async def capture_executor(self, task_input):
        await original_execute(self, task_input)
        observed["executor"] = self

    monkeypatch.setattr(WebSocketGraphExecutor, "execute_graph_async", capture_executor)

    class MessageSink:
        def __init__(self):
            self.events = []

        async def send_message(self, session_id, message):
            self.events.append(message)

        def send_message_sync(self, session_id, message):
            self.events.append(message)

    sink = MessageSink()
    task = "Compare option A with option B using the supplied design brief."
    asyncio.run(service.start_workflow("research-fixture", workflow.path.name, task, sink))

    assert store.get_session("research-fixture").status == SessionStatus.COMPLETED
    assert observed["executor"].get_final_output() == FINAL_OUTPUT
    assert any(
        message.role == MessageRole.USER and message.text_content() == task
        for message in observed["conversation"]
    )
    assert {tool.name for tool in observed["tools"]} == set(content.tools)
    assert {event["type"] for event in sink.events} >= {"workflow_started", "workflow_completed"}
    tool_execution.assert_not_awaited()
