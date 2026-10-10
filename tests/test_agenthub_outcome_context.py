"""T26: optional outcome recorder follows the real graph execution path."""

import asyncio
from uuid import uuid4

import yaml

from entity.graph_config import GraphConfig
from entity.messages import Message, MessageRole
from runtime.node.agent_outcome import AgentOutcomeRecorder
from runtime.node.executor.base import ExecutionContext
from workflow.graph import GraphExecutor
from workflow.graph_context import GraphContext

from server.services.attachment_service import AttachmentService
from server.services.session_execution import SessionExecutionController
from server.services.session_store import SessionStatus, WorkflowSessionStore
from server.services.websocket_executor import WebSocketGraphExecutor
from server.services.workflow_run_service import WorkflowRunService


def _definition():
    return {
        "id": "passthrough_fixture", "start": ["echo"], "end": ["echo"],
        "nodes": [{
            "id": "echo", "type": "passthrough",
            "config": {"only_last_message": True},
        }],
        "edges": [],
    }


def _graph(tmp_path, name):
    config = GraphConfig.from_dict(
        _definition(), name=name, output_root=tmp_path,
    )
    return GraphContext(config=config)


class MessageSink:
    def __init__(self):
        self.messages = []

    async def send_message(self, session_id, message):
        self.messages.append((session_id, message))

    def send_message_sync(self, session_id, message):
        self.messages.append((session_id, message))


def _capture_executors(monkeypatch):
    captured = []
    execute = WebSocketGraphExecutor.execute_graph_async

    async def run_and_capture(self, task_prompt):
        await execute(self, task_prompt)
        captured.append(self)

    monkeypatch.setattr(WebSocketGraphExecutor, "execute_graph_async", run_and_capture)
    return captured


def test_old_context_and_graph_execute_without_recorder_or_message_change(tmp_path):
    direct = ExecutionContext(tool_manager=None, function_manager=None, log_manager=None)
    graph = _graph(tmp_path, "default")
    executor = GraphExecutor(graph)

    executor.run("hello")
    output = executor.get_final_output_message()

    assert direct.outcome_recorder is None
    assert executor._get_execution_context().outcome_recorder is None
    assert isinstance(output, Message)
    assert output.role == MessageRole.USER
    assert output.text_content() == "hello"
    assert "outcome_recorder" not in graph.metadata


def test_injected_recorder_reaches_node_context_without_graph_metadata(tmp_path):
    recorder = AgentOutcomeRecorder(run_id=uuid4(), node_id="echo")
    graph = _graph(tmp_path, "injected")
    executor = GraphExecutor(graph, outcome_recorder=recorder)

    executor.run("hello")

    assert executor._get_execution_context().outcome_recorder is recorder
    assert executor.node_executors["passthrough"].context.outcome_recorder is recorder
    assert executor.get_final_output_message().text_content() == "hello"
    assert recorder.read().status == "missing"  # T27 will record at the Agent boundary.
    assert "outcome_recorder" not in graph.metadata


def test_web_workflow_propagates_recorder_and_isolates_two_runs(tmp_path, monkeypatch):
    workflow = tmp_path / "passthrough.yaml"
    workflow.write_text(yaml.safe_dump({
        "version": "0.4.0", "vars": {}, "graph": _definition(),
    }), encoding="utf-8")
    store = WorkflowSessionStore()
    controller = SessionExecutionController(store)
    attachments = AttachmentService(root=tmp_path / "attachments")
    service = WorkflowRunService(store, controller, attachments)
    sink = MessageSink()
    monkeypatch.setattr(service, "_resolve_yaml_path", lambda _: workflow)
    monkeypatch.setattr("server.services.workflow_run_service.WARE_HOUSE_DIR", tmp_path / "warehouse")
    captured = _capture_executors(monkeypatch)
    first = AgentOutcomeRecorder(run_id=uuid4(), node_id="echo")
    second = AgentOutcomeRecorder(run_id=uuid4(), node_id="echo")

    for session_id, recorder in (("first-session", first), ("second-session", second)):
        asyncio.run(service.start_workflow(
            session_id, workflow.name, "hello", sink, outcome_recorder=recorder,
        ))
        session = store.get_session(session_id)
        executor = captured[-1]
        assert session.status == SessionStatus.COMPLETED
        assert executor._get_execution_context().outcome_recorder is recorder
        assert executor.node_executors["passthrough"].context.outcome_recorder is recorder
        assert executor.get_final_output_message().text_content() == "hello"
        assert "outcome_recorder" not in executor.graph.metadata

    assert captured[0]._get_execution_context().outcome_recorder is first
    assert captured[1]._get_execution_context().outcome_recorder is second
    assert first.read().status == second.read().status == "missing"


def test_web_workflow_default_path_keeps_no_recorder_and_message(tmp_path, monkeypatch):
    workflow = tmp_path / "passthrough.yaml"
    workflow.write_text(yaml.safe_dump({
        "version": "0.4.0", "vars": {}, "graph": _definition(),
    }), encoding="utf-8")
    store = WorkflowSessionStore()
    service = WorkflowRunService(
        store, SessionExecutionController(store), AttachmentService(root=tmp_path / "attachments"),
    )
    monkeypatch.setattr(service, "_resolve_yaml_path", lambda _: workflow)
    monkeypatch.setattr("server.services.workflow_run_service.WARE_HOUSE_DIR", tmp_path / "warehouse")
    captured = _capture_executors(monkeypatch)

    asyncio.run(service.start_workflow("legacy-session", workflow.name, "hello", MessageSink()))

    session = store.get_session("legacy-session")
    assert session.status == SessionStatus.COMPLETED
    assert session.executor is None
    assert captured[0]._get_execution_context().outcome_recorder is None
    output = captured[0].get_final_output_message()
    assert isinstance(output, Message)
    assert output.role == MessageRole.USER
    assert output.text_content() == "hello"
