"""Persistent business submissions remain independent of Web sessions."""

import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.task_runs import TaskRun, TaskRunRepository


def _repository(tmp_path):
    database = AgentHubDatabase(tmp_path / "agenthub.db")
    return TaskRunRepository(database), database


def test_insert_get_and_reopen_preserve_business_fields(tmp_path):
    repository, database = _repository(tmp_path)
    attachment_id = uuid4().hex
    run = TaskRun.new(
        task="Summarize the incident report",
        strategy="semantic_llm",
        session_id=str(uuid4()),
        attachment_ids=(attachment_id,),
    )

    with database.transaction() as connection:
        repository.insert(connection, run)

    assert run.status == "pending"
    assert run.run_id != UUID(run.session_id)
    assert run.created_at.tzinfo == timezone.utc
    assert repository.get(run.run_id) == run
    reopened = TaskRunRepository(AgentHubDatabase(database.path))
    assert reopened.get(run.run_id) == run
    with database.connection() as connection:
        row = connection.execute(
            "SELECT attachment_ids_json, task FROM agenthub_task_runs WHERE run_id = ?",
            (str(run.run_id),),
        ).fetchone()
    assert row == ('["' + attachment_id + '"]', run.task)


def test_same_session_has_independent_runs_and_session_is_optional(tmp_path):
    repository, database = _repository(tmp_path)
    session_id = str(uuid4())
    first = TaskRun.new(task="First task", strategy="semantic", session_id=session_id)
    second = TaskRun.new(task="Second task", strategy="semantic", session_id=session_id)
    rejected_before_session = TaskRun.new(task="No match", strategy="semantic")

    with database.transaction() as connection:
        for run in (first, second, rejected_before_session):
            repository.insert(connection, run)

    assert len({first.run_id, second.run_id, rejected_before_session.run_id}) == 3
    assert repository.get(first.run_id) == first
    assert repository.get(second.run_id) == second
    assert repository.get(rejected_before_session.run_id) == rejected_before_session
    assert rejected_before_session.session_id is None


def test_unknown_id_and_invalid_lookup_are_explicit(tmp_path):
    repository, _ = _repository(tmp_path)
    assert repository.get(uuid4()) is None
    with pytest.raises(ValueError, match="run_id"):
        repository.get("not-a-uuid")


def test_duplicate_and_required_sql_fields_are_constrained(tmp_path):
    repository, database = _repository(tmp_path)
    run = TaskRun.new(task="Check constraints", strategy="semantic")
    with database.transaction() as connection:
        repository.insert(connection, run)

    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            repository.insert(connection, run)

    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO agenthub_task_runs
                   (run_id, task, attachment_ids_json, strategy, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(uuid4()), None, "[]", "semantic", "pending", run.created_at.isoformat()),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO agenthub_task_runs
                   (run_id, task, attachment_ids_json, strategy, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(uuid4()), "Invalid status", "[]", "semantic", "completed",
                 run.created_at.isoformat()),
            )
    assert repository.get(run.run_id) == run


@pytest.mark.parametrize(
    "status", ("pending", "running", "success", "failed", "rejected", "cancelled")
)
def test_all_six_business_statuses_round_trip(tmp_path, status):
    repository, database = _repository(tmp_path)
    run = TaskRun.model_validate({
        **TaskRun.new(task="Status sample", strategy="semantic").model_dump(),
        "status": status,
    })
    with database.transaction() as connection:
        repository.insert(connection, run)
    assert repository.get(run.run_id).status == status


@pytest.mark.parametrize(
    "changes",
    (
        {"run_id": UUID(int=0)},
        {"task": "  "},
        {"strategy": "unknown"},
        {"status": "completed"},
        {"attachment_ids": ("",)},
        {"session_id": ""},
        {"error_code": "raw exception: secret"},
        {"result_summary": "x" * 513},
        {"created_at": datetime(2026, 1, 1)},
    ),
)
def test_invalid_fields_are_rejected(changes):
    values = TaskRun.new(task="Valid", strategy="semantic").model_dump()
    values.update(changes)
    with pytest.raises(ValidationError):
        TaskRun.model_validate(values)


def test_optional_trace_result_and_error_references_are_small_and_utc(tmp_path):
    repository, database = _repository(tmp_path)
    created = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))
    run = TaskRun.model_validate({
        **TaskRun.new(task="Store references", strategy="semantic").model_dump(),
        "created_at": created,
        "started_at": created + timedelta(seconds=1),
        "finished_at": created + timedelta(seconds=2),
        "routing_trace_id": uuid4(),
        "agent_run_id": uuid4(),
        "error_code": "PROVIDER_FAILED",
        "error_message": "Provider unavailable",
        "result_ref": "artifact:summary-1",
        "result_summary": "Short result summary",
    })
    with database.transaction() as connection:
        repository.insert(connection, run)
    restored = repository.get(run.run_id)
    assert restored == run
    assert restored.created_at.utcoffset() == timedelta(0)
    assert restored.started_at.utcoffset() == timedelta(0)
    assert restored.finished_at.utcoffset() == timedelta(0)


def test_error_message_rejects_credential_like_text():
    values = TaskRun.new(task="Failure", strategy="semantic").model_dump()
    values["error_message"] = "provider api_key=REDACTED"
    with pytest.raises(ValidationError):
        TaskRun.model_validate(values)
