"""Final routing decisions are bounded, explainable, and durable."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.routing_traces import (
    RoutingTrace, RoutingTraceRepository, TraceAgent, TraceCandidate, TraceRerank,
    routing_elapsed_ms,
)
from server.services.agenthub.task_runs import TaskRun, TaskRunRepository


FIRST = TraceAgent(agent_id=UUID(int=1), version=2)
SECOND = TraceAgent(agent_id=UUID(int=2), version=1)
CANDIDATES = (
    TraceCandidate(agent=FIRST, raw_similarity=0.87),
    TraceCandidate(agent=SECOND, raw_similarity=0.72),
)


def _repositories(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    return database, TaskRunRepository(database), RoutingTraceRepository(database)


def _trace(run_id, **changes):
    values = dict(
        trace_id=uuid4(), run_id=run_id, strategy="semantic", status="selected",
        created_at=datetime.now(timezone.utc), routing_latency_ms=12.5,
        eligible_agents=(FIRST, SECOND), candidates=CANDIDATES,
        threshold_value=0.5, threshold_source="calibration-v1",
        embedding_model_key="embedding-v1", selected_agent=FIRST,
    )
    values.update(changes)
    return RoutingTrace(**values)


def test_selected_trace_round_trips_with_raw_scores_and_task_run_link(tmp_path):
    database, runs, traces = _repositories(tmp_path)
    run = TaskRun.new(task="Summarize report", strategy="semantic", attachment_ids=(uuid4().hex,))
    trace = _trace(run.run_id)
    with database.transaction() as connection:
        runs.insert(connection, run)
        traces.insert(connection, trace)

    assert traces.get_by_run_id(run.run_id) == trace
    assert runs.get(run.run_id).routing_trace_id == trace.trace_id
    reopened = RoutingTraceRepository(AgentHubDatabase(database.path))
    assert reopened.get_by_run_id(run.run_id) == trace
    assert tuple(item.raw_similarity for item in trace.candidates) == (0.87, 0.72)
    assert all(item.score_kind == "cosine" for item in trace.candidates)
    with database.connection() as connection:
        stored = connection.execute(
            "SELECT payload_json FROM agenthub_routing_traces WHERE run_id = ?",
            (str(run.run_id),),
        ).fetchone()[0]
    assert run.task not in stored
    assert run.attachment_ids[0] not in stored
    assert "description" not in stored
    assert "api_key" not in stored
    assert "runtime_ref" not in stored
    assert json.loads(stored)["routing_latency_ms"] == 12.5


@pytest.mark.parametrize(
    "status,fields",
    (
        ("rejected", dict(selected_agent=None, rejection_reason="NO_SUITABLE_AGENT")),
        ("rejected", dict(selected_agent=None, rejection_reason="NO_SUITABLE_AGENT",
                          candidates=(), eligible_agents=())),
        ("failed", dict(selected_agent=None, error_code="ROUTING_INFRASTRUCTURE_ERROR",
                        candidates=(), eligible_agents=(), threshold_value=None,
                        threshold_source=None, embedding_model_key=None)),
        ("failed", dict(selected_agent=FIRST, error_code="RUNTIME_REF_INVALID")),
    ),
)
def test_rejected_and_failed_attempts_have_final_trace(tmp_path, status, fields):
    database, runs, traces = _repositories(tmp_path)
    run = TaskRun.new(task="Attempt", strategy="semantic")
    trace = _trace(run.run_id, status=status, **fields)
    with database.transaction() as connection:
        runs.insert(connection, run)
        traces.insert(connection, trace)
    assert traces.get_by_run_id(run.run_id) == trace
    assert runs.get(run.run_id).routing_trace_id == trace.trace_id


def test_rerank_order_is_separate_from_recall_order_and_nullable(tmp_path):
    database, runs, traces = _repositories(tmp_path)
    first = TaskRun.new(task="Semantic", strategy="semantic")
    second = TaskRun.new(task="Rerank", strategy="semantic_llm")
    semantic = _trace(first.run_id)
    rerank = TraceRerank(
        input_candidates=(FIRST, SECOND), ordered_candidates=(SECOND, FIRST),
        model_key="rerank-v1", reason_code="CAPABILITY_MATCH",
    )
    semantic_llm = _trace(
        second.run_id, strategy="semantic_llm", selected_agent=SECOND, rerank=rerank,
    )
    with database.transaction() as connection:
        runs.insert(connection, first)
        runs.insert(connection, second)
        traces.insert(connection, semantic)
        traces.insert(connection, semantic_llm)
    assert traces.get_by_run_id(first.run_id).rerank is None
    restored = traces.get_by_run_id(second.run_id)
    assert restored.rerank.ordered_candidates == (SECOND, FIRST)
    assert restored.candidates == CANDIDATES
    assert restored.selected_agent == SECOND


def test_failed_rerank_preserves_attempted_input_without_invented_output(tmp_path):
    database, runs, traces = _repositories(tmp_path)
    run = TaskRun.new(task="Rerank timeout", strategy="semantic_llm")
    trace = _trace(
        run.run_id, strategy="semantic_llm", status="failed", selected_agent=None,
        error_code="RERANK_TIMEOUT", rerank=TraceRerank(
            input_candidates=(FIRST, SECOND), ordered_candidates=(), model_key="rerank-v1",
        ),
    )
    with database.transaction() as connection:
        runs.insert(connection, run)
        traces.insert(connection, trace)
    restored = traces.get_by_run_id(run.run_id)
    assert restored.rerank.input_candidates == (FIRST, SECOND)
    assert restored.rerank.ordered_candidates == ()
    assert restored.error_code == "RERANK_TIMEOUT"


def test_duplicate_final_notification_is_idempotent_but_conflict_fails(tmp_path):
    database, runs, traces = _repositories(tmp_path)
    run = TaskRun.new(task="Once", strategy="semantic")
    trace = _trace(run.run_id)
    with database.transaction() as connection:
        runs.insert(connection, run)
        traces.insert(connection, trace)
        traces.insert(connection, trace)
    with database.connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agenthub_routing_traces WHERE run_id = ?",
            (str(run.run_id),),
        ).fetchone()[0] == 1
    with pytest.raises(ValueError, match="final trace"):
        with database.transaction() as connection:
            traces.insert(connection, _trace(run.run_id))
    assert runs.get(run.run_id).routing_trace_id == trace.trace_id


def test_trace_requires_existing_task_run(tmp_path):
    database, _, traces = _repositories(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            traces.insert(connection, _trace(uuid4()))


def test_trace_strategy_must_match_task_run_request(tmp_path):
    database, runs, traces = _repositories(tmp_path)
    run = TaskRun.new(task="Semantic request", strategy="semantic")
    with database.transaction() as connection:
        runs.insert(connection, run)
    with pytest.raises(ValueError, match="strategy"):
        with database.transaction() as connection:
            traces.insert(connection, _trace(
                run.run_id, strategy="semantic_llm", selected_agent=None,
                status="failed", error_code="RERANK_TIMEOUT",
            ))
    assert traces.get_by_run_id(run.run_id) is None
    assert runs.get(run.run_id).routing_trace_id is None


@pytest.mark.parametrize(
    "changes",
    (
        {"selected_agent": FIRST, "rejection_reason": "NO_SUITABLE_AGENT"},
        {"status": "selected", "selected_agent": None},
        {"status": "rejected", "selected_agent": None},
        {"status": "failed", "selected_agent": None},
        {"threshold_value": 0.5, "threshold_source": None},
        {"routing_latency_ms": -1},
        {"routing_latency_ms": float("nan")},
        {"created_at": datetime(2026, 1, 1)},
        {"selected_agent": SECOND},
        {"embedding_model_key": "sk-" + "x" * 20},
        {"role": "private prompt"},
    ),
)
def test_invalid_trace_fields_are_rejected(changes):
    values = _trace(uuid4()).model_dump()
    values.update(changes)
    with pytest.raises(ValidationError):
        RoutingTrace.model_validate(values)


def test_candidate_scores_and_rerank_ids_are_validated():
    with pytest.raises(ValidationError):
        TraceCandidate(agent=FIRST, raw_similarity=1.1)
    with pytest.raises(ValidationError):
        TraceCandidate(agent=FIRST, raw_similarity=0.5, score_kind="probability")
    with pytest.raises(ValidationError):
        _trace(uuid4(), strategy="semantic_llm", rerank=TraceRerank(
            input_candidates=(FIRST, SECOND), ordered_candidates=(TraceAgent(agent_id=uuid4(), version=1),),
            model_key="rerank-v1", reason_code="CAPABILITY_MATCH",
        ))
    with pytest.raises(ValidationError):
        TraceRerank(
            input_candidates=(FIRST,), ordered_candidates=(FIRST,),
            model_key="sk-" + "x" * 20, reason_code="CAPABILITY_MATCH",
        )


def test_routing_elapsed_ms_uses_monotonic_seconds(monkeypatch):
    monkeypatch.setattr("server.services.agenthub.routing_traces.time.monotonic", lambda: 12.625)
    assert routing_elapsed_ms(12.0) == 625.0
    with pytest.raises(ValueError):
        routing_elapsed_ms(13.0)


def test_utc_timestamp_is_normalized():
    local = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))
    trace = _trace(uuid4(), created_at=local)
    assert trace.created_at.utcoffset() == timedelta(0)
