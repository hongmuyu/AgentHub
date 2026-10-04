"""Agent execution snapshots stay distinct from tasks and native sessions."""

import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from server.services.agenthub.agent_runs import AgentRun, AgentRunRepository, TokenUsageSummary
from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.metadata import AgentMetadata, AgentMetadataInput
from server.services.agenthub.task_runs import TaskRun, TaskRunRepository
from server.services.agenthub.versions import AgentVersionRepository


def _content(*, revision=1, name="Research Agent"):
    return AgentMetadataInput(
        name=name, description="Summarizes technical reports",
        capabilities=("summarize technical reports",),
        runtime_ref=f"workflow://research-agent/{revision}",
    )


def _repositories(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    versions = AgentVersionRepository(database)
    runs = TaskRunRepository(database)
    executions = AgentRunRepository(database)
    agent = AgentMetadata.register(_content())
    with database.transaction() as connection:
        versions.insert_initial(connection, agent)
    return database, versions, runs, executions, agent


def _execution(run, agent, **changes):
    values = dict(
        agent_run_id=uuid4(), run_id=run.run_id,
        agent_id=agent.snapshot.id, agent_version=agent.snapshot.version,
        runtime_ref=agent.snapshot.runtime_ref, session_id=run.session_id or str(uuid4()),
        workflow_id="thin_research", workflow_revision=1, node_id="research_agent",
        status="running", native_status="running",
        started_at=datetime.now(timezone.utc),
    )
    values.update(changes)
    return AgentRun(**values)


def test_agent_run_links_task_agent_version_workflow_and_reopens(tmp_path):
    database, versions, runs, executions, agent = _repositories(tmp_path)
    run = TaskRun.new(task="Summarize report", strategy="semantic", session_id=str(uuid4()))
    execution = _execution(run, agent)
    with database.transaction() as connection:
        runs.insert(connection, run)
        executions.insert(connection, execution)

    assert executions.get_by_run_id(run.run_id) == execution
    assert runs.get(run.run_id).agent_run_id == execution.agent_run_id
    assert execution.run_id != UUID(execution.session_id)
    assert execution.agent_id != execution.run_id
    assert versions.get_version(execution.agent_id, execution.agent_version) == agent.snapshot
    reopened = AgentRunRepository(AgentHubDatabase(database.path))
    assert reopened.get_by_run_id(run.run_id) == execution


def test_version_and_runtime_snapshot_survive_catalog_update_and_disable(tmp_path):
    database, versions, runs, executions, agent = _repositories(tmp_path)
    run = TaskRun.new(task="Original version", strategy="semantic")
    execution = _execution(run, agent)
    with database.transaction() as connection:
        runs.insert(connection, run)
        executions.insert(connection, execution)

    updated = agent.update_content(_content(revision=2, name="Updated Research Agent"))
    with database.transaction() as connection:
        versions.insert_next(connection, updated.snapshot, updated.updated_at)
        versions.update_status(connection, updated, updated.set_status("disabled"))

    restored = executions.get_by_run_id(run.run_id)
    assert restored == execution
    assert restored.agent_version == 1
    assert restored.runtime_ref == "workflow://research-agent/1"
    assert versions.get_current(agent.snapshot.id).snapshot.version == 2
    assert versions.get_current(agent.snapshot.id).status == "disabled"
    assert versions.get_version(agent.snapshot.id, 1) == agent.snapshot


def test_only_one_agent_run_per_task(tmp_path):
    database, _, runs, executions, agent = _repositories(tmp_path)
    run = TaskRun.new(task="One execution", strategy="semantic")
    first = _execution(run, agent)
    with database.transaction() as connection:
        runs.insert(connection, run)
        executions.insert(connection, first)
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            executions.insert(connection, _execution(run, agent))
    assert executions.get_by_run_id(run.run_id) == first
    assert runs.get(run.run_id).agent_run_id == first.agent_run_id


def test_execution_identity_snapshot_cannot_be_rewritten(tmp_path):
    database, _, runs, executions, agent = _repositories(tmp_path)
    run = TaskRun.new(task="Fixed snapshot", strategy="semantic")
    execution = _execution(run, agent)
    with database.transaction() as connection:
        runs.insert(connection, run)
        executions.insert(connection, execution)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.transaction() as connection:
            connection.execute(
                "UPDATE agenthub_agent_runs SET runtime_ref = ? WHERE run_id = ?",
                ("workflow://research-agent/2", str(run.run_id)),
            )
    assert executions.get_by_run_id(run.run_id) == execution


def test_rejected_task_cannot_create_agent_run(tmp_path):
    database, _, runs, executions, agent = _repositories(tmp_path)
    pending = TaskRun.new(task="No suitable agent", strategy="semantic")
    rejected = TaskRun.model_validate({**pending.model_dump(), "status": "rejected"})
    with database.transaction() as connection:
        runs.insert(connection, rejected)
    with pytest.raises(ValueError, match="rejected"):
        with database.transaction() as connection:
            executions.insert(connection, _execution(rejected, agent))
    assert executions.get_by_run_id(rejected.run_id) is None
    assert runs.get(rejected.run_id).agent_run_id is None


def test_unknown_agent_version_and_mismatched_runtime_ref_are_rejected(tmp_path):
    database, _, runs, executions, agent = _repositories(tmp_path)
    run = TaskRun.new(task="Validate snapshot", strategy="semantic")
    with database.transaction() as connection:
        runs.insert(connection, run)
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            executions.insert(connection, _execution(run, agent, agent_version=99))
    with pytest.raises(ValueError, match="runtime_ref"):
        with database.transaction() as connection:
            executions.insert(connection, _execution(
                run, agent, runtime_ref="workflow://other-agent/1",
            ))
    assert executions.get_by_run_id(run.run_id) is None


def test_session_correlation_must_match_when_task_has_session(tmp_path):
    database, _, runs, executions, agent = _repositories(tmp_path)
    run = TaskRun.new(task="Session correlation", strategy="semantic", session_id=str(uuid4()))
    with database.transaction() as connection:
        runs.insert(connection, run)
    with pytest.raises(ValueError, match="session_id"):
        with database.transaction() as connection:
            executions.insert(connection, _execution(run, agent, session_id=str(uuid4())))
    assert executions.get_by_run_id(run.run_id) is None


def test_agent_run_requires_an_existing_task_run(tmp_path):
    database, _, _, executions, agent = _repositories(tmp_path)
    missing = TaskRun.new(task="Not persisted", strategy="semantic")
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            executions.insert(connection, _execution(missing, agent))
    assert executions.get_by_run_id(missing.run_id) is None


def test_nullable_usage_distinguishes_unknown_from_known_zero(tmp_path):
    database, _, runs, executions, agent = _repositories(tmp_path)
    unknown_run = TaskRun.new(task="Unknown usage", strategy="semantic")
    zero_run = TaskRun.new(task="Known zero", strategy="semantic")
    unknown = _execution(unknown_run, agent)
    zero = _execution(
        zero_run, agent,
        token_usage=TokenUsageSummary(total_tokens=0, input_tokens=0, output_tokens=0),
    )
    with database.transaction() as connection:
        for run, execution in ((unknown_run, unknown), (zero_run, zero)):
            runs.insert(connection, run)
            executions.insert(connection, execution)
    assert executions.get_by_run_id(unknown_run.run_id).token_usage is None
    assert executions.get_by_run_id(zero_run.run_id).token_usage == zero.token_usage
    with database.connection() as connection:
        rows = connection.execute(
            "SELECT run_id, token_usage_json FROM agenthub_agent_runs"
        ).fetchall()
    stored = dict(rows)
    assert stored[str(unknown_run.run_id)] is None
    assert '"total_tokens":0' in stored[str(zero_run.run_id)]


def test_native_status_result_error_and_partial_usage_round_trip(tmp_path):
    database, _, runs, executions, agent = _repositories(tmp_path)
    run = TaskRun.new(task="Provider failure", strategy="semantic", session_id=str(uuid4()))
    started = run.created_at.astimezone(timezone(timedelta(hours=8))) + timedelta(seconds=1)
    execution = _execution(
        run, agent, status="failed", native_status="completed",
        started_at=started, finished_at=started + timedelta(seconds=2), latency_ms=2000.0,
        token_usage=TokenUsageSummary(total_tokens=7, output_tokens=4),
        result_ref="artifact:run-output", result_summary="Short output summary",
        error_code="PROVIDER_FAILED", error_message="Provider unavailable",
        native_error_code="NATIVE_PROVIDER_ERROR",
    )
    with database.transaction() as connection:
        runs.insert(connection, run)
        executions.insert(connection, execution)
    restored = executions.get_by_run_id(run.run_id)
    assert restored == execution
    assert restored.status == "failed" and restored.native_status == "completed"
    assert restored.token_usage.total_tokens == 7
    assert restored.token_usage.input_tokens is None
    assert restored.started_at.utcoffset() == timedelta(0)
    assert restored.finished_at.utcoffset() == timedelta(0)
    assert restored.latency_ms == 2000.0


@pytest.mark.parametrize(
    "changes",
    (
        {"agent_run_id": UUID(int=0)},
        {"agent_version": 0},
        {"runtime_ref": "/tmp/workflow.yaml"},
        {"workflow_revision": 0},
        {"workflow_revision": 2},
        {"session_id": ""},
        {"status": "rejected"},
        {"native_status": "success"},
        {"started_at": datetime(2026, 1, 1)},
        {"latency_ms": -1},
        {"result_summary": "x" * 513},
        {"result_ref": "/tmp/output.txt"},
        {"error_message": "api_key=REDACTED"},
        {"role": "private prompt"},
    ),
)
def test_invalid_execution_fields_are_rejected(tmp_path, changes):
    _, _, _, _, agent = _repositories(tmp_path)
    run = TaskRun.new(task="Validate", strategy="semantic")
    values = _execution(run, agent).model_dump()
    values.update(changes)
    with pytest.raises(ValidationError):
        AgentRun.model_validate(values)


def test_usage_values_are_nonnegative_and_nullable():
    assert TokenUsageSummary(total_tokens=0).input_tokens is None
    with pytest.raises(ValidationError):
        TokenUsageSummary(total_tokens=-1)
    with pytest.raises(ValidationError):
        TokenUsageSummary(total_tokens=1, input_tokens=-1)
