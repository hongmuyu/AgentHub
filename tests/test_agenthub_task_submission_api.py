"""Task submission preserves routing decisions before any workflow dispatch."""

import asyncio
import json
import time
from pathlib import Path
from threading import Event
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from entity.messages import Message, MessageRole
from runtime.node.agent import ModelResponse
from server.services.agenthub.agent_runs import AgentRunRepository
from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.discovery import DiscoveryCandidate, PublicAgentMetadata
from server.services.agenthub.metadata import AgentMetadata, AgentMetadataInput
from server.services.agenthub.reranker import RerankResult
from server.services.agenthub.router import CalibratedThreshold, SemanticLLMRouter, SemanticRouter
from server.services.agenthub.routing_traces import RoutingTraceRepository
from server.services.agenthub.runtime_resolver import RuntimeRefResolver
from server.services.agenthub.task_runs import TaskRunRepository
from server.services.agenthub.task_service import TaskSubmissionService
from server.services.agenthub.thin_workflow import ThinWorkflowValidator
from server.services.agenthub.versions import AgentVersionRepository
from server.services.attachment_service import AttachmentService
from server.services.session_store import SessionStatus, WorkflowSessionStore
from server.services.websocket_manager import WebSocketManager
from server.services.websocket_executor import WebSocketGraphExecutor
from server.routes import ALL_ROUTERS, agenthub_tasks


REFERENCE = "workflow://research-agent/1"
TASK = "Summarize the report"


class ScriptedDiscovery:
    def __init__(self):
        self.candidates = ()
        self.error = None
        self.calls = []
        self.before_return = None

    def discover(self, task, *, k):
        self.calls.append((task, k))
        if self.before_return is not None:
            self.before_return()
        if self.error is not None:
            raise self.error
        return self.candidates[:k]


class FakeDispatcher:
    def __init__(self):
        self.calls = []
        self.error = None
        self.before_return = None

    async def dispatch(self, context):
        self.calls.append(context)
        if self.before_return is not None:
            self.before_return(context)
        if self.error is not None:
            raise self.error


@pytest.fixture
def api(tmp_path):
    root = tmp_path / "workflows"
    root.mkdir()
    (root / "research.yaml").write_text(yaml.safe_dump({
        "version": "0.4.0", "vars": {},
        "graph": {
            "id": "thin_research", "start": ["agent"], "end": ["agent"],
            "nodes": [{
                "id": "agent", "type": "agent",
                "config": {"provider": "openai", "name": "fixture-model", "role": "Summarize."},
            }],
            "edges": [],
        },
    }), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({REFERENCE: "research.yaml"}), encoding="utf-8")
    validator = ThinWorkflowValidator(RuntimeRefResolver(root, manifest))
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    versions = AgentVersionRepository(database)
    agent = AgentMetadata.register(AgentMetadataInput(
        name="Research Agent", description="Summarizes reports",
        capabilities=("summarize reports",), runtime_ref=REFERENCE,
    ))
    with database.transaction() as connection:
        versions.insert_initial(connection, agent)
    candidate = DiscoveryCandidate(
        agent_id=agent.snapshot.id, version=1,
        public_metadata=PublicAgentMetadata(
            name="Research Agent", description="Summarizes reports",
            capabilities=("summarize reports",), tags=(), tools=(),
        ),
        raw_similarity=0.9,
    )
    discovery = ScriptedDiscovery()
    discovery.candidates = (candidate,)
    router = SemanticRouter(
        discovery, threshold=CalibratedThreshold(0.5, "fixture-calibration-v1"),
        top_k=3,
    )
    store = WorkflowSessionStore()
    attachments = AttachmentService(root=tmp_path / "warehouse")
    manager = WebSocketManager(session_store=store, attachment_service=attachments)
    session_id = str(uuid4())
    store.create_session(yaml_file="manual.yaml", task_prompt="", session_id=session_id)
    dispatcher = FakeDispatcher()
    service = TaskSubmissionService(
        database, manager, {"semantic": router}, validator, dispatcher,
        embedding_model_key="fake-v1",
    )
    app = FastAPI()
    app.state.agenthub_task_service = service
    app.include_router(agenthub_tasks.router)
    with TestClient(app) as client:
        yield {
            "client": client, "database": database, "versions": versions,
            "agent": agent, "candidate": candidate, "discovery": discovery,
            "dispatcher": dispatcher, "session_id": session_id,
            "manager": manager, "manifest": manifest,
            "service": service, "router": router,
            "runs": TaskRunRepository(database),
            "traces": RoutingTraceRepository(database),
            "executions": AgentRunRepository(database),
        }


def _submit(api, **changes):
    body = {"task": TASK, "session_id": api["session_id"], "attachments": [],
            "routing_strategy": "semantic"}
    body.update(changes)
    return api["client"].post("/api/agenthub/tasks", json=body)


def _run_count(database):
    with database.connection() as connection:
        return connection.execute("SELECT count(*) FROM agenthub_task_runs").fetchone()[0]


def test_task_router_is_registered():
    assert agenthub_tasks.router in ALL_ROUTERS


def test_selected_route_persists_trace_and_running_execution_before_one_dispatch(api):
    def inspect_before_return(context):
        run = api["runs"].get(context.run_id)
        trace = api["traces"].get_by_run_id(context.run_id)
        assert run.status == "running"
        assert trace.status == "selected"
        assert run.agent_run_id == context.agent_run_id

    api["dispatcher"].before_return = inspect_before_return
    attachment = api["manager"].attachment_service.get_attachment_store(
        api["session_id"]
    ).register_bytes(b"report", display_name="report.txt")
    response = _submit(api, attachments=[attachment.ref.attachment_id])
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert body["routing_status"] == "selected"
    assert body["session_id"] == api["session_id"]
    assert body["run_id"] != api["session_id"]
    assert body["selected_agent"] == {
        "id": str(api["agent"].snapshot.id), "version": 1, "name": "Research Agent"
    }
    assert "success" not in response.text
    assert "research.yaml" not in response.text
    run_id = UUID(body["run_id"])
    run = api["runs"].get(run_id)
    trace = api["traces"].get_by_run_id(run_id)
    execution = api["executions"].get_by_run_id(run_id)
    assert run.status == "running"
    assert run.attachment_ids == (attachment.ref.attachment_id,)
    assert run.routing_trace_id == trace.trace_id
    assert run.agent_run_id == execution.agent_run_id
    assert trace.selected_agent.agent_id == execution.agent_id == api["agent"].snapshot.id
    assert trace.selected_agent.version == execution.agent_version == 1
    assert trace.candidates[0].raw_similarity == 0.9
    assert execution.runtime_ref == REFERENCE
    assert len(api["dispatcher"].calls) == 1
    context = api["dispatcher"].calls[0]
    assert context.run_id == run_id
    assert context.session_id == api["session_id"]
    assert context.attachment_ids == (attachment.ref.attachment_id,)
    assert context.workflow.path.name == "research.yaml"
    assert context.task == TASK
    assert api["discovery"].calls == [(TASK, 3)]


def test_pending_run_exists_before_router_is_called(api):
    def inspect_pending():
        with api["database"].connection() as connection:
            rows = connection.execute(
                "SELECT status, routing_trace_id, agent_run_id FROM agenthub_task_runs"
            ).fetchall()
        assert rows == [("pending", None, None)]

    api["discovery"].before_return = inspect_pending
    response = _submit(api)
    assert response.status_code == 202
    assert response.json()["status"] == "running"


def test_rejected_route_persists_distinct_terminal_without_dispatch(api):
    api["discovery"].candidates = (
        DiscoveryCandidate(**{**api["candidate"].__dict__, "raw_similarity": 0.2}),
    )
    response = _submit(api)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "rejected"
    assert body["routing_status"] == "rejected"
    assert body["error_code"] == "NO_SUITABLE_AGENT"
    run_id = UUID(body["run_id"])
    assert api["runs"].get(run_id).status == "rejected"
    assert api["traces"].get_by_run_id(run_id).rejection_reason == "NO_SUITABLE_AGENT"
    assert api["executions"].get_by_run_id(run_id) is None
    assert api["dispatcher"].calls == []


def test_routing_infrastructure_failure_is_durable_and_safe(api):
    api["discovery"].error = RuntimeError("private provider detail")
    response = _submit(api)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["routing_status"] == "failed"
    assert body["error_code"] == "ROUTING_INFRASTRUCTURE_ERROR"
    assert "private provider detail" not in response.text
    run_id = UUID(body["run_id"])
    assert api["runs"].get(run_id).status == "failed"
    assert api["traces"].get_by_run_id(run_id).status == "failed"
    assert api["executions"].get_by_run_id(run_id) is None
    assert api["dispatcher"].calls == []


def test_semantic_llm_strategy_persists_rerank_and_dispatches_selected_agent(api):
    class Reranker:
        def rerank(self, task, candidates):
            return RerankResult(
                ordered_candidate_ids=(candidates[0].agent_id,),
                reason_code="CAPABILITY_MATCH",
            )

    api["service"].routers["semantic_llm"] = SemanticLLMRouter(api["router"], Reranker())
    api["service"].rerank_model_key = "rerank-v1"
    response = _submit(api, routing_strategy="semantic_llm")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    trace = api["traces"].get_by_run_id(UUID(body["run_id"]))
    assert trace.strategy == "semantic_llm"
    assert trace.rerank.model_key == "rerank-v1"
    assert trace.rerank.reason_code == "CAPABILITY_MATCH"
    assert trace.rerank.ordered_candidates == (trace.selected_agent,)
    assert len(api["dispatcher"].calls) == 1


def test_valid_but_unconfigured_strategy_does_not_create_run(api):
    response = _submit(api, routing_strategy="semantic_llm")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ROUTING_STRATEGY_NOT_CONFIGURED"
    assert _run_count(api["database"]) == 0
    assert api["dispatcher"].calls == []


def test_dispatch_failure_marks_selected_execution_failed_without_echoing_error(api):
    api["dispatcher"].error = RuntimeError("private dispatch detail")
    response = _submit(api)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "EXECUTION_DISPATCH_FAILED"
    assert "private dispatch detail" not in response.text
    run_id = UUID(body["run_id"])
    assert api["traces"].get_by_run_id(run_id).status == "selected"
    assert api["runs"].get(run_id).status == "failed"
    assert api["executions"].get_by_run_id(run_id).status == "failed"
    assert len(api["dispatcher"].calls) == 1


@pytest.mark.parametrize("changes", (
    {"session_id": None}, {"session_id": ""},
    {"session_id": "not-established"}, {"session_id": "../unsafe"},
    {"task": "   "}, {"routing_strategy": "unknown"},
    {"yaml_file": "research.yaml"}, {"path": "/tmp/research.yaml"},
))
def test_invalid_request_does_not_create_run_or_dispatch(api, changes):
    response = _submit(api, **changes)
    assert response.status_code in (400, 422)
    assert _run_count(api["database"]) == 0
    assert api["dispatcher"].calls == []


def test_cross_session_and_expired_attachment_are_rejected_before_run(api):
    other = str(uuid4())
    api["manager"].session_store.create_session(
        yaml_file="manual.yaml", task_prompt="", session_id=other
    )
    other_record = api["manager"].attachment_service.get_attachment_store(other).register_bytes(
        b"other", display_name="other.txt"
    )
    cross = _submit(api, attachments=[other_record.ref.attachment_id])
    assert cross.status_code == 422
    assert _run_count(api["database"]) == 0

    own_record = api["manager"].attachment_service.get_attachment_store(
        api["session_id"]
    ).register_bytes(b"expired", display_name="expired.txt")
    Path(own_record.ref.local_path).unlink()
    expired = _submit(api, attachments=[own_record.ref.attachment_id])
    assert expired.status_code == 422
    assert _run_count(api["database"]) == 0
    assert api["dispatcher"].calls == []


def test_selected_version_is_rechecked_before_dispatch(api):
    current = api["versions"].get_current(api["agent"].snapshot.id)
    with api["database"].transaction() as connection:
        api["versions"].update_status(connection, current, current.set_status("disabled"))
    response = _submit(api)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "SELECTED_AGENT_UNAVAILABLE"
    run_id = UUID(body["run_id"])
    assert api["traces"].get_by_run_id(run_id).status == "selected"
    assert api["executions"].get_by_run_id(run_id) is None
    assert api["dispatcher"].calls == []


def test_invalid_runtime_reference_after_selection_fails_before_dispatch(api):
    api["manifest"].write_text("{}", encoding="utf-8")
    response = _submit(api)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "RUNTIME_REF_INVALID"
    run_id = UUID(body["run_id"])
    assert api["traces"].get_by_run_id(run_id).status == "selected"
    assert api["executions"].get_by_run_id(run_id) is None
    assert api["dispatcher"].calls == []


def test_unconfigured_service_returns_safe_unavailable():
    app = FastAPI()
    app.include_router(agenthub_tasks.router)
    with TestClient(app) as client:
        response = client.post("/api/agenthub/tasks", json={
            "task": TASK, "session_id": str(uuid4()), "attachments": [],
            "routing_strategy": "semantic",
        })
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "TASK_SERVICE_NOT_CONFIGURED"


def _install_web_dispatcher(api, monkeypatch):
    from server.services.agenthub.workflow_dispatcher import AgentHubWorkflowDispatcher

    workflow_root = api["manifest"].parent / "workflows"
    service = api["manager"].workflow_run_service
    monkeypatch.setattr(service, "_resolve_yaml_path", lambda name: workflow_root / name)
    monkeypatch.setattr(
        "server.services.workflow_run_service.WARE_HOUSE_DIR",
        api["manifest"].parent / "results",
    )
    api["service"].dispatcher = AgentHubWorkflowDispatcher(
        api["database"], api["manager"], api["service"].validator,
    )
    return service


def _wait_for_run_status(api, run_id, status):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = api["runs"].get(run_id)
        if run.status == status:
            return run
        time.sleep(0.01)
    return api["runs"].get(run_id)


def test_selected_task_uses_real_web_chain_and_preserves_correlation_and_attachment_ids(
    api, monkeypatch,
):
    service = _install_web_dispatcher(api, monkeypatch)
    completed = Event()
    observed = {}
    model_calls = []

    class FixtureProvider:
        def __init__(self, config):
            self.config = config

        def create_client(self):
            return object()

        def call_model(self, client, **kwargs):
            model_calls.append(kwargs["conversation"])
            return ModelResponse(message=Message(role=MessageRole.ASSISTANT, content="answer"))

    monkeypatch.setattr(
        "runtime.node.executor.agent_executor.ProviderRegistry.get_provider",
        lambda name: FixtureProvider,
    )
    monkeypatch.setattr(api["manager"], "send_message_sync", lambda *args: None)
    original_start = service.start_workflow
    original_execute = WebSocketGraphExecutor.execute_graph_async

    async def capture_start(*args, **kwargs):
        observed["start"] = (args, kwargs)
        try:
            await original_start(*args, **kwargs)
        finally:
            completed.set()

    async def capture_execute(self, task_input):
        observed["executor"] = self
        observed["task_input"] = task_input
        await original_execute(self, task_input)

    monkeypatch.setattr(service, "start_workflow", capture_start)
    monkeypatch.setattr(WebSocketGraphExecutor, "execute_graph_async", capture_execute)
    attachment = api["manager"].attachment_service.get_attachment_store(
        api["session_id"]
    ).register_bytes(b"report", display_name="report.txt")

    response = _submit(api, attachments=[attachment.ref.attachment_id])

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert completed.wait(5)
    run_id = UUID(response.json()["run_id"])
    run = api["runs"].get(run_id)
    execution = api["executions"].get_by_run_id(run_id)
    session = api["manager"].session_store.get_session(api["session_id"])
    args, kwargs = observed["start"]
    recorder = kwargs["outcome_recorder"]
    assert run.status == "running"  # T29 owns successful terminal projection.
    assert run.agent_run_id == execution.agent_run_id
    assert execution.runtime_ref == REFERENCE
    assert execution.workflow_id == "thin_research"
    assert execution.node_id == "agent"
    assert execution.session_id == api["session_id"] != str(run_id)
    assert args == (api["session_id"], "research.yaml", TASK, api["manager"])
    assert kwargs["attachments"] == [attachment.ref.attachment_id]
    assert recorder.run_id == run_id
    assert recorder.node_id == execution.node_id
    assert recorder.read().outcome.state == "succeeded"
    assert observed["executor"].session_id == api["session_id"]
    assert observed["executor"]._get_execution_context().outcome_recorder is recorder
    assert session.status == SessionStatus.COMPLETED
    assert session.task_attachments == [attachment.ref.attachment_id]
    assert model_calls
    assert any(
        block.attachment and block.attachment.attachment_id == attachment.ref.attachment_id
        for message in observed["task_input"] for block in message.blocks()
    )
    assert {event["type"] for event in session.message_buffer} >= {
        "workflow_started", "workflow_completed",
    }


@pytest.mark.parametrize("change,expected_code", [
    ("manifest", "RUNTIME_REF_INVALID"),
    ("remapped", "RUNTIME_REF_INVALID"),
    ("web_target", "RUNTIME_REF_INVALID"),
    ("removed", "RUNTIME_REF_INVALID"),
    ("invalid_yaml", "RUNTIME_REF_INVALID"),
    ("disabled", "SELECTED_AGENT_UNAVAILABLE"),
    ("version", "SELECTED_AGENT_UNAVAILABLE"),
])
def test_prelaunch_revalidation_blocks_changed_target_and_persists_failed_selection(
    api, monkeypatch, change, expected_code,
):
    service = _install_web_dispatcher(api, monkeypatch)
    start_workflow = AsyncMock()
    monkeypatch.setattr(service, "start_workflow", start_workflow)
    original_start = api["service"].transitions.start

    def change_after_selection(run_id, execution):
        started = original_start(run_id, execution)
        if change == "manifest":
            api["manifest"].write_text("{}", encoding="utf-8")
        elif change == "remapped":
            root = api["manifest"].parent / "workflows"
            (root / "other.yaml").write_bytes((root / "research.yaml").read_bytes())
            api["manifest"].write_text(
                json.dumps({REFERENCE: "other.yaml"}), encoding="utf-8",
            )
        elif change == "web_target":
            root = api["manifest"].parent / "other_workflows"
            root.mkdir()
            source = api["manifest"].parent / "workflows" / "research.yaml"
            (root / "research.yaml").write_bytes(source.read_bytes())
            monkeypatch.setattr(service, "_resolve_yaml_path", lambda name: root / name)
        elif change == "removed":
            (api["manifest"].parent / "workflows" / "research.yaml").unlink()
        elif change == "invalid_yaml":
            (api["manifest"].parent / "workflows" / "research.yaml").write_text(
                "graph: [", encoding="utf-8",
            )
        elif change == "disabled":
            current = api["versions"].get_current(api["agent"].snapshot.id)
            with api["database"].transaction() as connection:
                api["versions"].update_status(
                    connection, current, current.set_status("disabled"),
                )
        else:
            current = api["versions"].get_current(api["agent"].snapshot.id)
            content = AgentMetadataInput.model_validate(
                current.snapshot.model_dump(exclude={"id", "version"})
            )
            updated = current.update_content(content)
            with api["database"].transaction() as connection:
                api["versions"].insert_next(connection, updated.snapshot, updated.updated_at)
        return started

    monkeypatch.setattr(api["service"].transitions, "start", change_after_selection)

    response = _submit(api)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == expected_code
    run_id = UUID(body["run_id"])
    assert api["runs"].get(run_id).status == "failed"
    assert api["traces"].get_by_run_id(run_id).status == "selected"
    assert api["executions"].get_by_run_id(run_id).status == "failed"
    assert api["manager"].session_store.get_session(api["session_id"]).status == SessionStatus.IDLE
    start_workflow.assert_not_awaited()


@pytest.mark.parametrize("failure", ("swallowed", "raised"))
def test_web_start_failure_is_persisted_without_private_error(api, monkeypatch, failure):
    service = _install_web_dispatcher(api, monkeypatch)
    completed = Event()
    original_start = service.start_workflow

    if failure == "swallowed":
        original_resolve = service._resolve_yaml_path
        calls = 0

        def fail_during_start(name):
            nonlocal calls
            calls += 1
            if calls == 1:
                return original_resolve(name)
            raise RuntimeError("synthetic-private-start-detail")

        monkeypatch.setattr(service, "_resolve_yaml_path", fail_during_start)

    async def start(*args, **kwargs):
        try:
            if failure == "raised":
                raise RuntimeError("synthetic-private-start-detail")
            await original_start(*args, **kwargs)
        finally:
            completed.set()

    monkeypatch.setattr(service, "start_workflow", start)

    response = _submit(api)

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert completed.wait(5)
    run_id = UUID(response.json()["run_id"])
    run = _wait_for_run_status(api, run_id, "failed")
    execution = api["executions"].get_by_run_id(run_id)
    assert run.status == execution.status == "failed"
    assert run.error_code == execution.error_code == "WORKFLOW_EXECUTION_ERROR"
    assert execution.native_status == ("error" if failure == "swallowed" else None)
    assert api["traces"].get_by_run_id(run_id).status == "selected"
    assert "synthetic-private-start-detail" not in run.model_dump_json()
    assert "synthetic-private-start-detail" not in execution.model_dump_json()


def test_legacy_workflow_execute_keeps_required_yaml_and_default_call(api, monkeypatch):
    from server.routes import execute

    completed = Event()
    observed = {}
    manager = api["manager"]

    def known_session(session_id, *, require_connection=False):
        assert session_id == api["session_id"]
        assert require_connection is True
        return manager

    async def start(*args, **kwargs):
        observed["call"] = (args, kwargs)
        completed.set()

    monkeypatch.setattr(execute, "ensure_known_session", known_session)
    monkeypatch.setattr(manager.workflow_run_service, "start_workflow", start)
    app = FastAPI()
    app.include_router(execute.router)
    with TestClient(app) as client:
        missing_yaml = client.post("/api/workflow/execute", json={
            "task_prompt": TASK, "session_id": api["session_id"],
        })
        response = client.post("/api/workflow/execute", json={
            "yaml_file": "manual.yaml", "task_prompt": TASK,
            "session_id": api["session_id"], "attachments": ["upload-id"],
        })
        assert completed.wait(5)

    assert missing_yaml.status_code == 422
    assert response.status_code == 200
    assert response.json()["status"] == "started"
    assert observed["call"] == (
        (api["session_id"], "manual.yaml", TASK, manager),
        {"attachments": ["upload-id"], "log_level": None},
    )


def test_shared_web_manager_retains_human_input_and_cancel_handlers(api):
    manager = api["manager"]
    session = manager.session_store.get_session(api["session_id"])
    assert manager.message_handler.workflow_run_service is manager.workflow_run_service
    assert manager.message_handler.session_controller is manager.session_controller

    manager.session_controller.set_waiting_for_input(api["session_id"], "human", {})
    asyncio.run(manager.message_handler.handle_message(
        api["session_id"], {"type": "human_input", "data": {"input": "approved"}}, manager,
    ))
    assert session.human_input_future.result() == {"text": "approved", "attachments": []}

    asyncio.run(manager.message_handler.handle_message(
        api["session_id"], {"type": "cancel"}, manager,
    ))
    assert session.cancel_event.is_set()
    assert session.status == SessionStatus.CANCELLED
