"""T44: persisted Registry routing facts agree across task, query and metrics APIs."""

import json
from unittest.mock import patch
from uuid import UUID

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import the runtime validator before routes to avoid the inherited import cycle.
from server.services.agenthub.thin_workflow import ThinWorkflowValidator

# isort: split
from server.routes import agenthub_metrics, agenthub_tasks
from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.discovery import AgentDiscovery, discovery_text
from server.services.agenthub.embeddings import FakeEmbeddingBackend
from server.services.agenthub.metadata import AgentMetadataInput
from server.services.agenthub.metrics import MetricsService
from server.services.agenthub.registry import AgentRegistry
from server.services.agenthub.router import CalibratedThreshold, SemanticRouter
from server.services.agenthub.run_query import RunQueryService
from server.services.agenthub.runtime_resolver import RuntimeRefResolver
from server.services.agenthub.task_service import TaskSubmissionService
from server.services.attachment_service import AttachmentService
from server.services.session_store import WorkflowSessionStore
from server.services.websocket_manager import WebSocketManager

REFERENCE = "workflow://research/1"
SELECT = "Find this technical report"
REJECT = "Book a calendar meeting"
INFRA_ERROR = "No configured fake vector"
SESSION = "t44-session"


class RecordingDispatcher:
    def __init__(self):
        self.calls = []
        self.error = None

    async def dispatch(self, context):
        self.calls.append(context)
        if self.error is not None:
            raise self.error


@pytest.fixture
def stack(tmp_path):
    root = tmp_path / "workflows"
    root.mkdir()
    (root / "research.yaml").write_text(yaml.safe_dump({
        "version": "0.4.0", "vars": {},
        "graph": {
            "id": "t44_research", "start": ["agent"], "end": ["agent"],
            "nodes": [{"id": "agent", "type": "agent", "config": {
                "provider": "openai", "name": "fixture-model", "role": "Analyze."
            }}], "edges": [],
        },
    }), encoding="utf-8")
    (root / "agenthub_manifest.json").write_text(
        json.dumps({REFERENCE: "research.yaml"}), encoding="utf-8",
    )
    old = AgentMetadataInput(
        name="Research", description="Finds reports",
        capabilities=("find reports",), runtime_ref=REFERENCE,
    )
    changed = AgentMetadataInput(
        name="Research", description="Compares reports",
        capabilities=("compare reports",), runtime_ref=REFERENCE,
    )
    backend = FakeEmbeddingBackend({
        discovery_text(old): (1.0, 0.0),
        discovery_text(changed): (0.0, 1.0),
        SELECT: (1.0, 0.0), REJECT: (-1.0, 0.0),
    }, model_key="t44-fake-v1", dimensions=2)
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    validator = ThinWorkflowValidator(RuntimeRefResolver(root))
    registry = AgentRegistry(database, validator, backend)
    agent = registry.register(old)
    router = SemanticRouter(
        AgentDiscovery(registry.versions, registry.index, validator, backend),
        threshold=CalibratedThreshold(0.5, "t44-fixture"), top_k=1,
    )
    sessions = WorkflowSessionStore()
    sessions.create_session(yaml_file="manual.yaml", task_prompt="", session_id=SESSION)
    manager = WebSocketManager(
        session_store=sessions,
        attachment_service=AttachmentService(root=tmp_path / "warehouse"),
    )
    dispatcher = RecordingDispatcher()
    service = TaskSubmissionService(
        database, manager, {"semantic": router}, validator, dispatcher,
        embedding_model_key=backend.model_key,
    )
    app = FastAPI()
    app.state.agenthub_task_service = service
    app.include_router(agenthub_tasks.router)
    with TestClient(app) as client:
        yield {
            "client": client, "database": database, "registry": registry,
            "agent": agent, "changed": changed, "dispatcher": dispatcher,
        }


def _submit(stack, task):
    return stack["client"].post("/api/agenthub/tasks", json={
        "task": task, "session_id": SESSION, "routing_strategy": "semantic",
        "attachments": [],
    })


def _reopened_public_reads(stack, run_ids):
    reopened = AgentHubDatabase(stack["database"].path)
    app = FastAPI()
    app.state.agenthub_run_query_service = RunQueryService(reopened)
    app.state.agenthub_metrics_service = MetricsService(reopened)
    app.include_router(agenthub_tasks.router)
    app.include_router(agenthub_metrics.router)
    with TestClient(app) as client:
        queries = {key: client.get(f"/api/agenthub/tasks/{run_id}")
                   for key, run_id in run_ids.items()}
        metrics = client.get("/api/agenthub/metrics")
    return queries, metrics


def test_failed_index_update_preserves_old_version_through_api_and_metrics(stack):
    registry = stack["registry"]
    agent_id = stack["agent"].snapshot.id
    with (
        patch.object(registry.index, "insert_many", side_effect=RuntimeError("injected")),
        pytest.raises(RuntimeError, match="injected"),
    ):
        registry.update(agent_id, stack["changed"])

    reopened = AgentRegistry(
        AgentHubDatabase(stack["database"].path), registry.validator, registry.backend,
    )
    assert reopened.get(agent_id).snapshot.version == 1
    assert reopened.versions.get_version(agent_id, 2) is None
    assert reopened.index.get(agent_id, 2, registry.backend.model_key) is None

    response = _submit(stack, SELECT)
    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert response.json()["selected_agent"]["version"] == 1
    assert len(stack["dispatcher"].calls) == 1
    run_id = UUID(response.json()["run_id"])
    queries, metrics = _reopened_public_reads(stack, {"selected": run_id})
    assert queries["selected"].status_code == metrics.status_code == 200
    query = queries["selected"].json()
    assert query["status"] == query["agent_run"]["status"] == "running"
    assert query["agent_run"]["agent_version"] == 1
    assert query["routing_trace"]["selected_agent"] == {
        "agent_id": str(agent_id), "version": 1,
    }
    assert metrics.json()["agent_usage"] == [{
        "agent_id": str(agent_id), "version": 1, "started_count": 1,
    }]


def test_reject_infra_error_and_dispatch_failure_have_distinct_durable_facts(stack):
    rejected = _submit(stack, REJECT)
    assert rejected.status_code == 202
    assert rejected.json()["status"] == "rejected"
    assert stack["dispatcher"].calls == []

    routing_error = _submit(stack, INFRA_ERROR)
    assert routing_error.status_code == 202
    assert routing_error.json()["status"] == "failed"
    assert routing_error.json()["routing_status"] == "failed"
    assert stack["dispatcher"].calls == []

    stack["dispatcher"].error = RuntimeError("private startup detail")
    dispatch_error = _submit(stack, SELECT)
    assert dispatch_error.status_code == 202
    assert dispatch_error.json()["status"] == "failed"
    assert len(stack["dispatcher"].calls) == 1

    queries, metrics = _reopened_public_reads(stack, {
        "rejected": UUID(rejected.json()["run_id"]),
        "routing_error": UUID(routing_error.json()["run_id"]),
        "dispatch_error": UUID(dispatch_error.json()["run_id"]),
    })
    assert all(response.status_code == 200 for response in queries.values())
    reject_body = queries["rejected"].json()
    routing_body = queries["routing_error"].json()
    dispatch_body = queries["dispatch_error"].json()
    assert reject_body["status"] == reject_body["routing_trace"]["status"] == "rejected"
    assert reject_body["agent_run"] is None
    assert routing_body["status"] == routing_body["routing_trace"]["status"] == "failed"
    assert routing_body["agent_run"] is None
    assert dispatch_body["status"] == dispatch_body["agent_run"]["status"] == "failed"
    assert dispatch_body["routing_trace"]["status"] == "selected"
    assert dispatch_body["error_code"] == "EXECUTION_DISPATCH_FAILED"
    assert "private startup detail" not in str([response.json() for response in queries.values()])
    report = metrics.json()
    assert report["total_runs"] == 3
    assert report["run_status_counts"] == {
        "pending": 0, "running": 0, "success": 0,
        "failed": 2, "rejected": 1, "cancelled": 0,
    }
    assert report["routing_distribution"]["routing_failures"] == 1
    assert report["routing_distribution"]["rejection_reasons"] == {
        "NO_SUITABLE_AGENT": 1,
    }
