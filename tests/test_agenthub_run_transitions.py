"""Business run transitions commit the first terminal state atomically."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest

from server.services.agenthub.agent_runs import AgentRun, AgentRunRepository, TokenUsageSummary
from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.metadata import AgentMetadata, AgentMetadataInput
from server.services.agenthub.run_transitions import InvalidRunTransition, RunTransitionService
from server.services.agenthub.task_runs import TaskRun, TaskRunRepository
from server.services.agenthub.versions import AgentVersionRepository
from server.services.session_store import SessionStatus


def _fixture(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    versions = AgentVersionRepository(database)
    tasks = TaskRunRepository(database)
    executions = AgentRunRepository(database)
    service = RunTransitionService(database)
    agent = AgentMetadata.register(AgentMetadataInput(
        name="Research Agent", description="Summarizes technical reports",
        capabilities=("summarize reports",), runtime_ref="workflow://research-agent/1",
    ))
    task = TaskRun.new(task="Summarize report", strategy="semantic", session_id=str(uuid4()))
    with database.transaction() as connection:
        versions.insert_initial(connection, agent)
        tasks.insert(connection, task)
    execution = AgentRun(
        agent_run_id=uuid4(), run_id=task.run_id, agent_id=agent.snapshot.id,
        agent_version=1, runtime_ref=agent.snapshot.runtime_ref,
        session_id=task.session_id, workflow_id="thin_research", workflow_revision=1,
        node_id="research_agent", status="running", native_status="running",
        started_at=task.created_at + timedelta(seconds=1),
    )
    return database, tasks, executions, service, task, execution


def _start(fixture):
    _, tasks, executions, service, task, execution = fixture
    started = service.start(task.run_id, execution)
    assert started.status == "running"
    assert started.started_at == execution.started_at
    assert started.agent_run_id == execution.agent_run_id
    assert tasks.get(task.run_id) == started
    assert executions.get_by_run_id(task.run_id) == execution
    return started


def _finish(service, task, execution, status, *, at=None, **changes):
    fields = dict(
        at=at or execution.started_at + timedelta(seconds=3), latency_ms=3000.0,
        native_status="completed" if status != "cancelled" else "cancelled",
    )
    if status == "success":
        fields.update(workflow_completed=True, agent_outcome="succeeded")
    elif status == "failed":
        fields.update(error_code="PROVIDER_FAILED")
    fields.update(changes)
    return service.finish(task.run_id, status, **fields)


@pytest.mark.parametrize(
    "status,error_code",
    (
        ("rejected", "NO_SUITABLE_AGENT"),
        ("failed", "ROUTING_INFRASTRUCTURE_ERROR"),
        ("cancelled", None),
    ),
)
def test_pending_terminal_transitions_keep_distinct_statuses(tmp_path, status, error_code):
    _, tasks, executions, service, task, _ = _fixture(tmp_path)
    ended = service.finish(
        task.run_id, status, at=task.created_at + timedelta(seconds=1),
        error_code=error_code,
    )
    assert ended.status == status
    assert ended.error_code == error_code
    assert ended.finished_at == task.created_at + timedelta(seconds=1)
    assert ended.started_at is None
    assert ended.agent_run_id is None
    assert tasks.get(task.run_id) == ended
    assert executions.get_by_run_id(task.run_id) is None


@pytest.mark.parametrize("status", ("success", "failed", "cancelled"))
def test_running_terminal_updates_task_and_agent_together(tmp_path, status):
    fixture = _fixture(tmp_path)
    _, tasks, executions, service, task, execution = fixture
    _start(fixture)
    ended = _finish(
        service, task, execution, status,
        token_usage=TokenUsageSummary(total_tokens=0, input_tokens=0, output_tokens=0),
        result_ref="artifact:output", result_summary="Short result",
    )
    agent_run = executions.get_by_run_id(task.run_id)
    assert ended.status == agent_run.status == status
    assert ended.finished_at == agent_run.finished_at
    assert agent_run.latency_ms == 3000.0
    assert agent_run.token_usage.total_tokens == 0
    assert ended.result_ref == agent_run.result_ref == "artifact:output"
    assert ended.error_code == agent_run.error_code
    assert tasks.get(task.run_id) == ended
    if status == "failed":
        assert agent_run.native_status == "completed"


def test_start_same_execution_is_idempotent_but_another_execution_is_rejected(tmp_path):
    fixture = _fixture(tmp_path)
    _, tasks, executions, service, task, execution = fixture
    first = _start(fixture)
    assert service.start(task.run_id, execution) == first
    different = execution.model_copy(update={"agent_run_id": uuid4()})
    with pytest.raises(InvalidRunTransition):
        service.start(task.run_id, different)
    changed_snapshot = execution.model_copy(update={"workflow_id": "other_workflow"})
    with pytest.raises(InvalidRunTransition):
        service.start(task.run_id, changed_snapshot)
    assert tasks.get(task.run_id) == first
    assert executions.get_by_run_id(task.run_id) == execution


def test_repeated_start_remains_idempotent_after_native_status_changes(tmp_path):
    fixture = _fixture(tmp_path)
    _, tasks, executions, service, task, execution = fixture
    first = _start(fixture)
    service.record_native_status(task.run_id, "waiting_for_input")
    assert service.start(task.run_id, execution) == first
    assert tasks.get(task.run_id) == first
    assert executions.get_by_run_id(task.run_id).native_status == "waiting_for_input"


def test_invalid_edges_are_rejected_without_writes(tmp_path):
    fixture = _fixture(tmp_path)
    _, tasks, executions, service, task, execution = fixture
    with pytest.raises(InvalidRunTransition):
        _finish(service, task, execution, "success")
    assert tasks.get(task.run_id).status == "pending"

    running = _start(fixture)
    with pytest.raises(InvalidRunTransition):
        service.finish(task.run_id, "rejected", at=execution.started_at + timedelta(seconds=1),
                       error_code="NO_SUITABLE_AGENT", latency_ms=1000.0)
    assert tasks.get(task.run_id) == running
    assert executions.get_by_run_id(task.run_id) == execution


@pytest.mark.parametrize("first_status,second_status", (
    ("success", "cancelled"), ("cancelled", "success"),
))
def test_first_terminal_wins_in_both_arrival_orders(tmp_path, first_status, second_status):
    fixture = _fixture(tmp_path)
    _, tasks, executions, service, task, execution = fixture
    _start(fixture)
    first = _finish(service, task, execution, first_status)
    with pytest.raises(InvalidRunTransition):
        _finish(service, task, execution, second_status,
                at=execution.started_at + timedelta(seconds=4))
    assert tasks.get(task.run_id) == first
    assert executions.get_by_run_id(task.run_id).status == first_status


@pytest.mark.parametrize("status", ("success", "failed", "cancelled"))
def test_repeated_terminal_notification_is_idempotent(tmp_path, status):
    fixture = _fixture(tmp_path)
    _, tasks, executions, service, task, execution = fixture
    _start(fixture)
    first = _finish(service, task, execution, status)
    duplicate = _finish(
        service, task, execution, status,
        at=execution.started_at + timedelta(seconds=9),
    )
    assert duplicate == first
    assert tasks.get(task.run_id) == first
    assert executions.get_by_run_id(task.run_id).finished_at == first.finished_at


def test_repeated_pending_terminal_notification_is_idempotent(tmp_path):
    _, tasks, executions, service, task, _ = _fixture(tmp_path)
    first = service.finish(
        task.run_id, "rejected", at=task.created_at + timedelta(seconds=1),
        error_code="NO_SUITABLE_AGENT",
    )
    duplicate = service.finish(
        task.run_id, "rejected", at=task.created_at + timedelta(seconds=9),
        error_code="NO_SUITABLE_AGENT",
    )
    assert duplicate == first == tasks.get(task.run_id)
    assert executions.get_by_run_id(task.run_id) is None


@pytest.mark.parametrize("terminal_native_status", (None, "completed"))
def test_native_failure_cannot_be_recorded_as_business_success(
    tmp_path, terminal_native_status
):
    fixture = _fixture(tmp_path)
    _, tasks, executions, service, task, execution = fixture
    _start(fixture)
    service.record_native_status(task.run_id, "error")
    with pytest.raises(ValueError, match="success evidence"):
        _finish(service, task, execution, "success", native_status=terminal_native_status)
    assert tasks.get(task.run_id).status == "running"
    assert executions.get_by_run_id(task.run_id).native_status == "error"


def test_concurrent_completion_and_cancellation_commit_one_terminal(tmp_path):
    fixture = _fixture(tmp_path)
    _, tasks, executions, service, task, execution = fixture
    _start(fixture)
    barrier = Barrier(2)

    def attempt(status):
        barrier.wait(timeout=5)
        try:
            return _finish(service, task, execution, status).status
        except InvalidRunTransition:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(attempt, ("success", "cancelled")))
    assert results.count("conflict") == 1
    assert set(results) in ({"success", "conflict"}, {"cancelled", "conflict"})
    final = tasks.get(task.run_id)
    assert final.status in {"success", "cancelled"}
    assert executions.get_by_run_id(task.run_id).status == final.status


def test_native_waiting_and_completed_do_not_imply_business_success(tmp_path):
    fixture = _fixture(tmp_path)
    _, tasks, executions, service, task, execution = fixture
    _start(fixture)
    service.record_native_status(task.run_id, SessionStatus.WAITING_FOR_INPUT.value)
    assert tasks.get(task.run_id).status == "running"
    assert executions.get_by_run_id(task.run_id).native_status == "waiting_for_input"
    service.record_native_status(task.run_id, SessionStatus.COMPLETED.value)
    assert tasks.get(task.run_id).status == "running"
    assert executions.get_by_run_id(task.run_id).native_status == "completed"
    with pytest.raises(ValueError, match="success evidence"):
        service.finish(
            task.run_id, "success", at=execution.started_at + timedelta(seconds=3),
            latency_ms=3000.0, native_status="completed",
        )
    assert tasks.get(task.run_id).status == "running"
    assert executions.get_by_run_id(task.run_id).status == "running"


def test_success_requires_both_workflow_and_structured_agent_outcome(tmp_path):
    fixture = _fixture(tmp_path)
    _, tasks, _, service, task, execution = fixture
    _start(fixture)
    for workflow_completed, agent_outcome in (
        (False, "succeeded"), (True, None), (True, "failed")
    ):
        with pytest.raises(ValueError, match="success evidence"):
            service.finish(
                task.run_id, "success", at=execution.started_at + timedelta(seconds=3),
                latency_ms=3000.0, native_status="completed",
                workflow_completed=workflow_completed, agent_outcome=agent_outcome,
            )
    assert tasks.get(task.run_id).status == "running"


def test_terminal_agent_write_failure_rolls_back_task_status(tmp_path):
    fixture = _fixture(tmp_path)
    database, tasks, executions, service, task, execution = fixture
    _start(fixture)
    with database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER abort_agent_finish BEFORE UPDATE OF status ON agenthub_agent_runs
               WHEN NEW.status = 'success'
               BEGIN SELECT RAISE(ABORT, 'injected AgentRun failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected AgentRun failure"):
        _finish(service, task, execution, "success")
    assert tasks.get(task.run_id).status == "running"
    assert tasks.get(task.run_id).finished_at is None
    assert executions.get_by_run_id(task.run_id) == execution


def test_start_task_write_failure_rolls_back_agent_insert(tmp_path):
    fixture = _fixture(tmp_path)
    database, tasks, executions, service, task, execution = fixture
    with database.transaction() as connection:
        connection.execute(
            """CREATE TRIGGER abort_task_start BEFORE UPDATE OF status ON agenthub_task_runs
               WHEN NEW.status = 'running'
               BEGIN SELECT RAISE(ABORT, 'injected TaskRun failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected TaskRun failure"):
        service.start(task.run_id, execution)
    assert tasks.get(task.run_id) == task
    assert executions.get_by_run_id(task.run_id) is None


@pytest.mark.parametrize("status,error_code", (
    ("rejected", "NO_SUITABLE_AGENT"),
    ("failed", "ROUTING_INFRASTRUCTURE_ERROR"),
    ("cancelled", None),
))
def test_pending_terminal_cannot_start_later(tmp_path, status, error_code):
    fixture = _fixture(tmp_path)
    _, tasks, executions, service, task, execution = fixture
    first = service.finish(
        task.run_id, status, at=task.created_at + timedelta(seconds=1),
        error_code=error_code,
    )
    with pytest.raises(InvalidRunTransition):
        service.start(task.run_id, execution)
    assert tasks.get(task.run_id) == first
    assert executions.get_by_run_id(task.run_id) is None
