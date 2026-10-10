"""Atomic business run transitions, separate from native workflow status."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from .agent_runs import AgentRun, AgentRunRepository, TokenUsageSummary
from .database import AgentHubDatabase
from .task_runs import TaskRun, TaskRunRepository


TerminalStatus = Literal["success", "failed", "rejected", "cancelled"]
_TERMINAL = frozenset(("success", "failed", "rejected", "cancelled"))
_FROM_PENDING = frozenset(("rejected", "failed", "cancelled"))
_FROM_RUNNING = frozenset(("success", "failed", "cancelled"))
_EXECUTION_IDENTITY = frozenset((
    "agent_run_id", "run_id", "agent_id", "agent_version", "runtime_ref",
    "session_id", "workflow_id", "workflow_revision", "node_id", "started_at",
))


class InvalidRunTransition(ValueError):
    """The requested business status cannot follow the committed status."""


class RunTransitionService:
    def __init__(self, database: AgentHubDatabase) -> None:
        self.database = database
        self.tasks = TaskRunRepository(database)
        self.executions = AgentRunRepository(database)

    def start(self, run_id: UUID, execution: AgentRun) -> TaskRun:
        if execution.run_id != run_id or execution.status != "running":
            raise ValueError("AgentRun must be running and belong to the TaskRun")
        if execution.finished_at is not None:
            raise ValueError("AgentRun must not already be finished")
        with self.database.transaction() as connection:
            task = self._required_task(run_id, connection)
            if task.status == "running" and task.agent_run_id == execution.agent_run_id:
                stored = self.executions.get_by_run_id(run_id, connection=connection)
                if stored is not None and (
                    stored.model_dump(include=_EXECUTION_IDENTITY)
                    == execution.model_dump(include=_EXECUTION_IDENTITY)
                ):
                    return task
                raise InvalidRunTransition("AgentRun ID already belongs to a different snapshot")
            if task.status != "pending":
                raise InvalidRunTransition(f"cannot start a {task.status} TaskRun")
            updated = TaskRun.model_validate({
                **task.model_dump(), "status": "running",
                "started_at": execution.started_at,
                "session_id": task.session_id or execution.session_id,
                "agent_run_id": execution.agent_run_id,
            })
            self.executions.insert(connection, execution)
            changed = connection.execute(
                """UPDATE agenthub_task_runs SET status = 'running', started_at = ?,
                          session_id = ?
                   WHERE run_id = ? AND status = 'pending' AND agent_run_id = ?""",
                (updated.started_at.isoformat(), updated.session_id,
                 str(run_id), str(execution.agent_run_id)),
            )
            if changed.rowcount != 1:
                raise InvalidRunTransition("TaskRun could not start")
            return updated

    def finish(
        self, run_id: UUID, status: TerminalStatus, *, at: datetime,
        latency_ms: float | None = None, native_status: str | None = None,
        error_code: str | None = None, error_message: str | None = None,
        result_ref: str | None = None, result_summary: str | None = None,
        token_usage: TokenUsageSummary | None = None,
        workflow_completed: bool = False, agent_outcome: str | None = None,
    ) -> TaskRun:
        if status not in _TERMINAL:
            raise ValueError("status must be terminal")
        with self.database.transaction() as connection:
            task = self._required_task(run_id, connection)
            if task.status in _TERMINAL:
                if task.status == status:
                    return task
                raise InvalidRunTransition(f"TaskRun already ended as {task.status}")
            allowed = _FROM_PENDING if task.status == "pending" else _FROM_RUNNING
            if status not in allowed:
                raise InvalidRunTransition(f"cannot move {task.status} TaskRun to {status}")
            if status == "success":
                if not workflow_completed or agent_outcome != "succeeded":
                    raise ValueError("success evidence requires completed workflow and succeeded Agent")
                if error_code is not None or error_message is not None or native_status in ("error", "cancelled"):
                    raise ValueError("success evidence conflicts with failure or cancellation")
            if agent_outcome is not None and (
                (agent_outcome == "succeeded" and status != "success")
                or (agent_outcome == "failed" and status != "failed")
                or agent_outcome not in ("succeeded", "failed")
            ):
                raise ValueError("structured outcome must match terminal status")
            if status == "rejected" and error_code != "NO_SUITABLE_AGENT":
                raise ValueError("rejection requires NO_SUITABLE_AGENT")
            if status == "failed" and error_code is None:
                raise ValueError("failure requires an error_code")
            if task.status == "pending" and (
                latency_ms is not None or native_status is not None or token_usage is not None
                or agent_outcome is not None
            ):
                raise ValueError("pending TaskRun has no AgentRun execution data")
            if task.status == "pending" and task.agent_run_id is not None:
                raise RuntimeError("pending TaskRun cannot finish with an AgentRun")

            updated_task = TaskRun.model_validate({
                **task.model_dump(), "status": status, "finished_at": at,
                "error_code": error_code, "error_message": error_message,
                "result_ref": result_ref, "result_summary": result_summary,
            })
            updated_execution = None
            if task.status == "running":
                execution = self.executions.get_by_run_id(run_id, connection=connection)
                if execution is None or execution.agent_run_id != task.agent_run_id:
                    raise RuntimeError("running TaskRun must have its selected AgentRun")
                if status == "success" and execution.native_status in ("error", "cancelled"):
                    raise ValueError("success evidence conflicts with native failure or cancellation")
                updated_execution = AgentRun.model_validate({
                    **execution.model_dump(), "status": status,
                    "outcome_state": agent_outcome,
                    "native_status": native_status if native_status is not None else execution.native_status,
                    "finished_at": at, "latency_ms": latency_ms,
                    "token_usage": token_usage, "error_code": error_code,
                    "error_message": error_message, "result_ref": result_ref,
                    "result_summary": result_summary,
                })
                if status == "success" and updated_execution.native_status in ("error", "cancelled"):
                    raise ValueError("success evidence conflicts with native failure or cancellation")

            changed = connection.execute(
                """UPDATE agenthub_task_runs SET status = ?, finished_at = ?,
                          error_code = ?, error_message = ?, result_ref = ?, result_summary = ?
                   WHERE run_id = ? AND status = ?""",
                (status, updated_task.finished_at.isoformat(), error_code, error_message,
                 result_ref, result_summary, str(run_id), task.status),
            )
            if changed.rowcount != 1:
                raise InvalidRunTransition("TaskRun terminal commit lost its race")
            if updated_execution is not None:
                changed = connection.execute(
                    """UPDATE agenthub_agent_runs SET status = ?, outcome_state = ?, native_status = ?,
                              finished_at = ?, latency_ms = ?, token_usage_json = ?,
                              error_code = ?, error_message = ?, result_ref = ?, result_summary = ?
                       WHERE agent_run_id = ? AND status = 'running'""",
                    (status, updated_execution.outcome_state, updated_execution.native_status,
                     updated_execution.finished_at.isoformat(), updated_execution.latency_ms,
                     updated_execution.token_usage.model_dump_json()
                     if updated_execution.token_usage is not None else None,
                     error_code, error_message, result_ref, result_summary,
                     str(updated_execution.agent_run_id)),
                )
                if changed.rowcount != 1:
                    raise InvalidRunTransition("AgentRun terminal commit lost its race")
            return updated_task

    def record_native_status(self, run_id: UUID, native_status: str) -> AgentRun:
        with self.database.transaction() as connection:
            task = self._required_task(run_id, connection)
            if task.status != "running":
                raise InvalidRunTransition("native status can only update a running TaskRun")
            execution = self.executions.get_by_run_id(run_id, connection=connection)
            if execution is None:
                raise RuntimeError("running TaskRun must have its selected AgentRun")
            updated = AgentRun.model_validate({
                **execution.model_dump(), "native_status": native_status,
            })
            changed = connection.execute(
                """UPDATE agenthub_agent_runs SET native_status = ?
                   WHERE agent_run_id = ? AND status = 'running'""",
                (updated.native_status, str(updated.agent_run_id)),
            )
            if changed.rowcount != 1:
                raise InvalidRunTransition("AgentRun native status update lost its race")
            return updated

    def _required_task(self, run_id: UUID, connection) -> TaskRun:
        task = self.tasks.get(run_id, connection=connection)
        if task is None:
            raise LookupError("TaskRun not found")
        return task
