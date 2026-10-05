"""Task submission preserves routing decisions before any workflow dispatch."""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
from server.services.session_store import WorkflowSessionStore
from server.services.websocket_manager import WebSocketManager
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
