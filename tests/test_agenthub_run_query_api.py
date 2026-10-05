"""T24: query persisted business runs without exposing stored private fields."""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.services.agenthub.agent_runs import AgentRun
from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.metadata import AgentMetadata, AgentMetadataInput
from server.services.agenthub.routing_traces import RoutingTrace, RoutingTraceRepository, TraceAgent, TraceCandidate
from server.services.agenthub.run_query import RunQueryService
from server.services.agenthub.run_transitions import RunTransitionService
from server.services.agenthub.task_runs import TaskRun, TaskRunRepository
from server.services.agenthub.thin_workflow import ThinWorkflowValidator  # initialize existing runtime imports
from server.services.agenthub.versions import AgentVersionRepository
from server.routes import agenthub_tasks


def _client(database):
    app = FastAPI()
    app.state.agenthub_run_query_service = RunQueryService(database)
    app.include_router(agenthub_tasks.router)
    return TestClient(app)


def _stored_run(database, status):
    versions = AgentVersionRepository(database)
    tasks = TaskRunRepository(database)
    traces = RoutingTraceRepository(database)
    transitions = RunTransitionService(database)
    agent = AgentMetadata.register(AgentMetadataInput(
        name="Research Agent", description="Summarizes reports",
        capabilities=("summarize reports",), runtime_ref="workflow://research-agent/1",
    ))
    run = TaskRun.new(task="Private incident report", strategy="semantic", session_id=str(uuid4()))
    candidate = TraceAgent(agent_id=agent.snapshot.id, version=1)
    trace = RoutingTrace(
        trace_id=uuid4(), run_id=run.run_id, strategy="semantic",
        status="rejected" if status == "rejected" else "selected",
        created_at=run.created_at, routing_latency_ms=12.0,
        eligible_agents=(candidate,),
        candidates=(TraceCandidate(agent=candidate, raw_similarity=0.9),),
        selected_agent=None if status == "rejected" else candidate,
        rejection_reason="NO_SUITABLE_AGENT" if status == "rejected" else None,
    )
    with database.transaction() as connection:
        versions.insert_initial(connection, agent)
        tasks.insert(connection, run)
        traces.insert(connection, trace)
    if status == "rejected":
        transitions.finish(run.run_id, "rejected", at=run.created_at + timedelta(seconds=1),
                           error_code="NO_SUITABLE_AGENT")
    else:
        execution = AgentRun(
            agent_run_id=uuid4(), run_id=run.run_id, agent_id=candidate.agent_id,
            agent_version=1, runtime_ref=agent.snapshot.runtime_ref,
            session_id=run.session_id, workflow_id="thin_research", workflow_revision=1,
            node_id="agent", status="running", native_status="running",
            started_at=run.created_at + timedelta(seconds=1),
        )
        transitions.start(run.run_id, execution)
        if status != "running":
            transitions.finish(
                run.run_id, status, at=execution.started_at + timedelta(seconds=2),
                latency_ms=2000, native_status="cancelled" if status == "cancelled" else "completed",
                error_code="EXECUTION_FAILED" if status == "failed" else None,
                result_ref="artifact:result-1", result_summary="Safe result",
                workflow_completed=status == "success",
                agent_outcome="succeeded" if status == "success" else None,
            )
    return run.run_id, candidate


@pytest.mark.parametrize("status", ("running", "success", "failed", "rejected", "cancelled"))
def test_query_states_and_public_field_whitelist(tmp_path, status):
    database = AgentHubDatabase(tmp_path / "runs.db")
    run_id, candidate = _stored_run(database, status)
    with _client(database) as client:
        response = client.get(f"/api/agenthub/tasks/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "run_id", "session_id", "status", "routing_strategy", "created_at",
        "started_at", "finished_at", "error_code", "result_ref",
        "routing_trace", "agent_run",
    }
    assert body["run_id"] == str(run_id)
    assert body["status"] == status
    assert body["routing_trace"]["status"] == ("rejected" if status == "rejected" else "selected")
    assert set(body["routing_trace"]) == {
        "status", "routing_latency_ms", "candidates", "selected_agent",
        "rejection_reason", "error_code",
    }
    assert body["routing_trace"]["candidates"] == [{
        "agent_id": str(candidate.agent_id), "version": 1,
        "raw_similarity": 0.9, "score_kind": "cosine",
    }]
    assert body["routing_trace"]["selected_agent"] == (
        None if status == "rejected" else {"agent_id": str(candidate.agent_id), "version": 1}
    )
    assert body["result_ref"] == (None if status in ("running", "rejected") else "artifact:result-1")
    assert body["error_code"] == (
        "NO_SUITABLE_AGENT" if status == "rejected" else
        "EXECUTION_FAILED" if status == "failed" else None
    )
    if status == "rejected":
        assert body["agent_run"] is None
        assert body["routing_trace"]["rejection_reason"] == "NO_SUITABLE_AGENT"
    else:
        assert set(body["agent_run"]) == {
            "agent_run_id", "agent_id", "agent_version", "status", "native_status",
            "started_at", "finished_at", "latency_ms", "error_code", "result_ref",
        }
        assert body["agent_run"]["status"] == status
        assert body["agent_run"]["native_status"] == (
            "running" if status == "running" else "cancelled" if status == "cancelled" else "completed"
        )
    assert "Private incident report" not in response.text
    assert "workflow://" not in response.text
    assert "Safe result" not in response.text


def test_unknown_and_invalid_run_id_return_safe_errors(tmp_path):
    database = AgentHubDatabase(tmp_path / "runs.db")
    with _client(database) as client:
        assert client.get(f"/api/agenthub/tasks/{uuid4()}").json() == {
            "detail": {"code": "RUN_NOT_FOUND"}
        }
        response = client.get("/api/agenthub/tasks/not-a-uuid")
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "INVALID_RUN_ID"}}


def test_query_survives_database_reopen(tmp_path):
    path = tmp_path / "runs.db"
    run_id, _ = _stored_run(AgentHubDatabase(path), "failed")
    with _client(AgentHubDatabase(path)) as client:
        response = client.get(f"/api/agenthub/tasks/{run_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["agent_run"]["status"] == "failed"


def test_unsafe_stored_result_ref_has_safe_error_response(tmp_path):
    database = AgentHubDatabase(tmp_path / "runs.db")
    run_id, _ = _stored_run(database, "rejected")
    with database.transaction() as connection:
        connection.execute("UPDATE agenthub_task_runs SET result_ref = ? WHERE run_id = ?",
                           ("/tmp/private-result", str(run_id)))
    with _client(database) as client:
        response = client.get(f"/api/agenthub/tasks/{run_id}")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "RUN_DATA_INVALID"}}
    assert "/tmp/private-result" not in response.text


def test_inconsistent_trace_and_unsafe_session_are_not_exposed(tmp_path):
    database = AgentHubDatabase(tmp_path / "runs.db")
    run_id, _ = _stored_run(database, "running")
    with database.transaction() as connection:
        connection.execute("UPDATE agenthub_task_runs SET session_id = ? WHERE run_id = ?",
                           ("/tmp/private-session", str(run_id)))
    with _client(database) as client:
        response = client.get(f"/api/agenthub/tasks/{run_id}")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "RUN_DATA_INVALID"}}
    assert "/tmp/private-session" not in response.text

    with database.transaction() as connection:
        connection.execute("UPDATE agenthub_task_runs SET session_id = ?, routing_trace_id = ? WHERE run_id = ?",
                           (str(uuid4()), str(uuid4()), str(run_id)))
    with _client(database) as client:
        response = client.get(f"/api/agenthub/tasks/{run_id}")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "RUN_DATA_INVALID"}}
