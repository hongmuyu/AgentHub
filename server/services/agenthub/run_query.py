"""Read a persisted AgentHub run through an explicit public response contract."""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .agent_runs import AgentRun, AgentRunRepository
from .database import AgentHubDatabase
from .routing_traces import RoutingTrace, RoutingTraceRepository, TraceAgent, TraceCandidate
from .task_runs import TaskRun, TaskRunRepository


_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class RunQueryDataError(ValueError):
    """Stored run data cannot be exposed as a consistent public snapshot."""


class PublicCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    version: int
    raw_similarity: float
    score_kind: Literal["cosine"]


class PublicRoutingTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["selected", "rejected", "failed"]
    routing_latency_ms: float
    candidates: tuple[PublicCandidate, ...]
    selected_agent: TraceAgent | None
    rejection_reason: str | None
    error_code: str | None


class PublicAgentRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_run_id: UUID
    agent_id: UUID
    agent_version: int
    status: Literal["running", "success", "failed", "cancelled"]
    native_status: Literal[
        "idle", "running", "waiting_for_input", "completed", "error", "cancelled"
    ] | None
    started_at: datetime
    finished_at: datetime | None
    latency_ms: float | None
    error_code: str | None
    result_ref: str | None


class RunQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    session_id: str | None
    status: Literal["pending", "running", "success", "failed", "rejected", "cancelled"]
    routing_strategy: Literal["semantic", "semantic_llm"]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    result_ref: str | None
    routing_trace: PublicRoutingTrace | None
    agent_run: PublicAgentRun | None


class RunQueryService:
    def __init__(self, database: AgentHubDatabase) -> None:
        self.database = database
        self.tasks = TaskRunRepository(database)
        self.traces = RoutingTraceRepository(database)
        self.executions = AgentRunRepository(database)

    def get(self, run_id: UUID) -> RunQueryResponse | None:
        with self.database.connection() as connection:
            connection.execute("BEGIN")
            run = self.tasks.get(run_id, connection=connection)
            if run is None:
                return None
            trace = self.traces.get_by_run_id(run_id, connection=connection)
            execution = self.executions.get_by_run_id(run_id, connection=connection)

        self._check_links(run, trace, execution)
        try:
            # TaskRun can contain a legacy free-text ref; public refs must be logical keys.
            AgentRun.valid_result_ref(run.result_ref)
        except ValueError as exc:
            raise RunQueryDataError("unsafe result reference") from exc
        if run.session_id is not None and _SESSION_ID.fullmatch(run.session_id) is None:
            raise RunQueryDataError("unsafe session identifier")

        public_trace = None
        if trace is not None:
            public_trace = PublicRoutingTrace(
                status=trace.status, routing_latency_ms=trace.routing_latency_ms,
                candidates=tuple(self._candidate(item) for item in trace.candidates),
                selected_agent=trace.selected_agent,
                rejection_reason=trace.rejection_reason, error_code=trace.error_code,
            )
        public_execution = None
        if execution is not None:
            public_execution = PublicAgentRun(
                agent_run_id=execution.agent_run_id, agent_id=execution.agent_id,
                agent_version=execution.agent_version, status=execution.status,
                native_status=execution.native_status, started_at=execution.started_at,
                finished_at=execution.finished_at, latency_ms=execution.latency_ms,
                error_code=execution.error_code, result_ref=execution.result_ref,
            )
        return RunQueryResponse(
            run_id=run.run_id, session_id=run.session_id, status=run.status,
            routing_strategy=run.strategy, created_at=run.created_at,
            started_at=run.started_at, finished_at=run.finished_at,
            error_code=run.error_code, result_ref=run.result_ref,
            routing_trace=public_trace, agent_run=public_execution,
        )

    @staticmethod
    def _candidate(candidate: TraceCandidate) -> PublicCandidate:
        return PublicCandidate(
            agent_id=candidate.agent.agent_id, version=candidate.agent.version,
            raw_similarity=candidate.raw_similarity, score_kind=candidate.score_kind,
        )

    @staticmethod
    def _check_links(
        run: TaskRun, trace: RoutingTrace | None, execution: AgentRun | None
    ) -> None:
        if (trace is None) != (run.routing_trace_id is None):
            raise RunQueryDataError("trace link mismatch")
        if trace is not None and (
            trace.trace_id != run.routing_trace_id or trace.run_id != run.run_id
            or trace.strategy != run.strategy
            or (trace.status == "rejected" and run.status != "rejected")
            or (trace.status == "failed" and run.status != "failed")
            or (trace.status == "selected" and run.status == "rejected")
        ):
            raise RunQueryDataError("trace mismatch")
        if (execution is None) != (run.agent_run_id is None):
            raise RunQueryDataError("execution link mismatch")
        if execution is not None and (
            execution.agent_run_id != run.agent_run_id or execution.run_id != run.run_id
            or execution.status != run.status
            or (trace is not None and trace.selected_agent != TraceAgent(
                agent_id=execution.agent_id, version=execution.agent_version
            ))
        ):
            raise RunQueryDataError("execution mismatch")
        if run.status in ("running", "success") and execution is None:
            raise RunQueryDataError("execution missing")
