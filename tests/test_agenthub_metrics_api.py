"""T33: business metrics use persisted facts and explicit sample counts."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.services.agenthub.thin_workflow import ThinWorkflowValidator  # initialize existing runtime imports
from server.routes import agenthub_metrics
from server.services.agenthub.agent_runs import AgentRun, AgentRunRepository, TokenUsageSummary
from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.metadata import AgentMetadata, AgentMetadataInput
from server.services.agenthub.metrics import MetricsService
from server.services.agenthub.routing_traces import (
    RoutingTrace, RoutingTraceRepository, TraceAgent, TraceCandidate,
)
from server.services.agenthub.run_transitions import RunTransitionService
from server.services.agenthub.task_runs import TaskRun, TaskRunRepository
from server.services.agenthub.versions import AgentVersionRepository


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _client(database):
    app = FastAPI()
    app.state.agenthub_metrics_service = MetricsService(database)
    app.include_router(agenthub_metrics.router)
    return TestClient(app)


def _seed(database):
    versions = AgentVersionRepository(database)
    tasks = TaskRunRepository(database)
    traces = RoutingTraceRepository(database)
    transitions = RunTransitionService(database)
    agent = AgentMetadata.register(AgentMetadataInput(
        name="Research", description="Researches reports",
        capabilities=("research reports",), runtime_ref="workflow://research/1",
    ))
    selected = TraceAgent(agent_id=agent.snapshot.id, version=1)
    with database.transaction() as connection:
        versions.insert_initial(connection, agent)

    def add(index, *, status, strategy="semantic", trace_status="selected",
            outcome=None, tokens=None, latency_ms=None, rejection_reason=None):
        created = BASE + timedelta(minutes=index)
        run = TaskRun(
            run_id=uuid4(), task=f"task {index}", strategy=strategy,
            status="pending", created_at=created, session_id=f"session-{index}",
        )
        with database.transaction() as connection:
            tasks.insert(connection, run)
            if trace_status is not None:
                trace = RoutingTrace(
                    trace_id=uuid4(), run_id=run.run_id, strategy=strategy,
                    status=trace_status, created_at=created,
                    routing_latency_ms=10.0 * (index + 1),
                    eligible_agents=(selected,),
                    candidates=(TraceCandidate(agent=selected, raw_similarity=0.9),),
                    selected_agent=selected if trace_status == "selected" else None,
                    rejection_reason=rejection_reason,
                )
                traces.insert(connection, trace)
        if status == "pending":
            return run
        if status == "rejected":
            transitions.finish(run.run_id, status, at=created + timedelta(seconds=1),
                               error_code="NO_SUITABLE_AGENT")
            return run
        started = created + timedelta(seconds=1)
        execution = AgentRun(
            agent_run_id=uuid4(), run_id=run.run_id, agent_id=selected.agent_id,
            agent_version=1, runtime_ref=agent.snapshot.runtime_ref,
            session_id=run.session_id, workflow_id="research", workflow_revision=1,
            node_id="agent", status="running", started_at=started,
        )
        transitions.start(run.run_id, execution)
        if status != "running":
            transitions.finish(
                run.run_id, status, at=started + timedelta(milliseconds=latency_ms),
                latency_ms=latency_ms, token_usage=tokens,
                native_status="cancelled" if status == "cancelled" else "completed",
                error_code="PROVIDER_FAILED" if status == "failed" else None,
                workflow_completed=status == "success", agent_outcome=outcome,
            )
        return run

    add(0, status="success", outcome="succeeded", latency_ms=300_000,
        tokens=TokenUsageSummary(total_tokens=0, input_tokens=0, output_tokens=0))
    add(1, status="failed", outcome="failed", latency_ms=1000,
        tokens=TokenUsageSummary(total_tokens=12, input_tokens=10))
    add(2, status="failed", latency_ms=2000)
    add(3, status="cancelled", latency_ms=4000)
    add(4, status="rejected", strategy="semantic_llm", trace_status="rejected",
        rejection_reason="NO_SUITABLE_AGENT")
    add(5, status="running")
    add(6, status="pending", trace_status=None)
    return selected


def test_mixed_facts_match_hand_calculated_metrics_and_api(tmp_path):
    database = AgentHubDatabase(tmp_path / "metrics.db")
    agent = _seed(database)
    with _client(database) as client:
        response = client.get("/api/agenthub/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["total_runs"] == 7
    assert body["run_status_counts"] == {
        "pending": 1, "running": 1, "success": 1, "failed": 2,
        "rejected": 1, "cancelled": 1,
    }
    assert body["execution_success_rate"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert body["execution_failure_rate"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert body["rejected_rate"] == {"numerator": 1, "denominator": 7, "value": 1 / 7}
    assert body["average_routing_latency_ms"] == {"sample_count": 6, "value": 35.0}
    assert body["average_execution_latency_ms"] == {"sample_count": 4, "value": 76_750.0}
    assert body["token_usage"] == {
        "total": {"sum": 12, "known_count": 2, "unknown_count": 3},
        "input": {"sum": 10, "known_count": 2, "unknown_count": 3},
        "output": {"sum": 0, "known_count": 1, "unknown_count": 4},
    }
    assert body["agent_usage"] == [{
        "agent_id": str(agent.agent_id), "version": 1, "started_count": 5,
    }]
    assert body["routing_distribution"] == {
        "strategies": {"semantic": 6, "semantic_llm": 1},
        "selected_agents": [{"agent_id": str(agent.agent_id), "version": 1, "count": 5}],
        "rejection_reasons": {"NO_SUITABLE_AGENT": 1},
        "routing_failures": 0,
    }
    assert body["task_quality_success_rate"] is None


def test_empty_window_and_validation(tmp_path):
    database = AgentHubDatabase(tmp_path / "empty.db")
    with _client(database) as client:
        response = client.get("/api/agenthub/metrics")
        invalid = client.get("/api/agenthub/metrics?start=2026-01-02T00:00:00Z&end=2026-01-01T00:00:00Z")
    assert response.status_code == 200
    body = response.json()
    assert body["total_runs"] == 0
    assert body["execution_success_rate"] == {"numerator": 0, "denominator": 0, "value": None}
    assert body["average_execution_latency_ms"] == {"sample_count": 0, "value": None}
    assert body["token_usage"]["total"] == {"sum": None, "known_count": 0, "unknown_count": 0}
    assert body["task_quality_success_rate"] is None
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": {"code": "INVALID_METRICS_WINDOW"}}


def test_window_uses_task_acceptance_time(tmp_path):
    database = AgentHubDatabase(tmp_path / "window.db")
    _seed(database)
    with _client(database) as client:
        response = client.get(
            "/api/agenthub/metrics",
            params={"start": "2026-01-01T00:02:00Z", "end": "2026-01-01T00:05:00Z"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["total_runs"] == 3
    assert body["run_status_counts"]["rejected"] == 1
    assert body["execution_success_rate"]["denominator"] == 0
    assert body["average_execution_latency_ms"] == {"sample_count": 2, "value": 3000.0}


def test_old_agent_run_table_migrates_without_inventing_outcome(tmp_path):
    database = AgentHubDatabase(tmp_path / "legacy.db")
    _seed(database)
    with database.transaction() as connection:
        connection.execute("ALTER TABLE agenthub_agent_runs RENAME COLUMN outcome_state TO old_outcome")
    AgentRunRepository(database)
    with database.connection() as connection:
        states = connection.execute(
            "SELECT outcome_state FROM agenthub_agent_runs"
        ).fetchall()
    assert states == [(None,)] * 5
    assert MetricsService(database).get().execution_success_rate.denominator == 0


def test_agent_versions_and_routing_infrastructure_failures_are_separate(tmp_path):
    database = AgentHubDatabase(tmp_path / "groups.db")
    first = _seed(database)
    versions = AgentVersionRepository(database)
    current = versions.get_current(first.agent_id)
    updated = current.update_content(AgentMetadataInput(
        name="Research v2", description="Researches reports",
        capabilities=("research reports",), runtime_ref="workflow://research/2",
    ))
    with database.transaction() as connection:
        versions.insert_next(connection, updated.snapshot, updated.updated_at)

    tasks = TaskRunRepository(database)
    traces = RoutingTraceRepository(database)
    transitions = RunTransitionService(database)
    second = TraceAgent(agent_id=first.agent_id, version=2)
    run = TaskRun(run_id=uuid4(), task="new version", strategy="semantic",
                  status="pending", created_at=BASE + timedelta(minutes=7), session_id="session-v2")
    failed_route = TaskRun(run_id=uuid4(), task="route unavailable", strategy="semantic_llm",
                           status="pending", created_at=BASE + timedelta(minutes=8))
    with database.transaction() as connection:
        tasks.insert(connection, run)
        traces.insert(connection, RoutingTrace(
            trace_id=uuid4(), run_id=run.run_id, strategy="semantic", status="selected",
            created_at=run.created_at, routing_latency_ms=80,
            eligible_agents=(second,),
            candidates=(TraceCandidate(agent=second, raw_similarity=0.9),),
            selected_agent=second,
        ))
        tasks.insert(connection, failed_route)
        traces.insert(connection, RoutingTrace(
            trace_id=uuid4(), run_id=failed_route.run_id, strategy="semantic_llm",
            status="failed", created_at=failed_route.created_at,
            routing_latency_ms=90, eligible_agents=(), candidates=(),
            error_code="ROUTING_INFRASTRUCTURE_ERROR",
        ))
    transitions.start(run.run_id, AgentRun(
        agent_run_id=uuid4(), run_id=run.run_id, agent_id=second.agent_id,
        agent_version=2, runtime_ref="workflow://research/2", session_id="session-v2",
        workflow_id="research", workflow_revision=2, node_id="agent",
        status="running", started_at=run.created_at + timedelta(seconds=1),
    ))
    transitions.finish(failed_route.run_id, "failed", at=failed_route.created_at + timedelta(seconds=1),
                       error_code="ROUTING_INFRASTRUCTURE_ERROR")

    with _client(database) as client:
        body = client.get("/api/agenthub/metrics").json()
    assert body["agent_usage"] == [
        {"agent_id": str(first.agent_id), "version": 1, "started_count": 5},
        {"agent_id": str(first.agent_id), "version": 2, "started_count": 1},
    ]
    assert body["routing_distribution"]["selected_agents"] == [
        {"agent_id": str(first.agent_id), "version": 1, "count": 5},
        {"agent_id": str(first.agent_id), "version": 2, "count": 1},
    ]
    assert body["routing_distribution"]["routing_failures"] == 1
    assert body["execution_failure_rate"]["denominator"] == 2
