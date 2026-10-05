"""Persist AgentHub task routing before dispatching a selected workflow."""

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.services.websocket_manager import WebSocketManager

from .agent_runs import AgentRun
from .database import AgentHubDatabase
from .metadata import AgentMetadataVersion
from .router import RoutingResult
from .routing_traces import (
    RoutingTrace, RoutingTraceRepository, TraceAgent, TraceCandidate, TraceRerank,
    routing_elapsed_ms,
)
from .run_transitions import RunTransitionService
from .task_runs import TaskRun, TaskRunRepository
from .thin_workflow import ThinWorkflowValidator, ValidatedWorkflow
from .versions import AgentVersionRepository


_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_ATTACHMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


class TaskSubmissionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    task: str = Field(max_length=10000)
    session_id: str
    attachments: tuple[str, ...] = ()
    routing_strategy: Literal["semantic", "semantic_llm"] = "semantic"

    @field_validator("task")
    @classmethod
    def valid_task(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task must not be blank")
        return value

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str) -> str:
        if _SESSION_ID.fullmatch(value) is None:
            raise ValueError("session_id must be a bounded logical identifier")
        return value

    @field_validator("attachments")
    @classmethod
    def valid_attachments(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values) or any(
            _ATTACHMENT_ID.fullmatch(value) is None for value in values
        ):
            raise ValueError("attachments must contain distinct attachment IDs")
        return values


class CandidateSummary(BaseModel):
    id: UUID
    version: int
    name: str
    raw_similarity: float


class SelectedAgentSummary(BaseModel):
    id: UUID
    version: int
    name: str


class TaskSubmissionResponse(BaseModel):
    run_id: UUID
    session_id: str
    status: Literal["running", "rejected", "failed"]
    routing_status: Literal["selected", "rejected", "failed"]
    selected_agent: SelectedAgentSummary | None = None
    candidates: tuple[CandidateSummary, ...] = ()
    error_code: str | None = None


class TaskSubmissionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TaskRouter(Protocol):
    def route(self, task: str) -> RoutingResult: ...


@dataclass(frozen=True)
class DispatchContext:
    run_id: UUID
    agent_run_id: UUID
    session_id: str
    task: str
    attachment_ids: tuple[str, ...]
    agent: AgentMetadataVersion
    workflow: ValidatedWorkflow


class TaskDispatcher(Protocol):
    async def dispatch(self, context: DispatchContext) -> None: ...


class TaskSubmissionService:
    def __init__(
        self,
        database: AgentHubDatabase,
        manager: WebSocketManager,
        routers: Mapping[str, TaskRouter],
        validator: ThinWorkflowValidator,
        dispatcher: TaskDispatcher,
        *,
        embedding_model_key: str,
        rerank_model_key: str | None = None,
    ) -> None:
        self.database = database
        self.manager = manager
        self.routers = routers
        self.validator = validator
        self.dispatcher = dispatcher
        self.embedding_model_key = embedding_model_key
        self.rerank_model_key = rerank_model_key
        self.runs = TaskRunRepository(database)
        self.traces = RoutingTraceRepository(database)
        self.versions = AgentVersionRepository(database)
        self.transitions = RunTransitionService(database)

    async def submit(self, request: TaskSubmissionInput) -> TaskSubmissionResponse:
        self._validate_session_and_attachments(request)
        router = self.routers.get(request.routing_strategy)
        if router is None:
            raise TaskSubmissionError("ROUTING_STRATEGY_NOT_CONFIGURED")

        run = TaskRun.new(
            task=request.task, strategy=request.routing_strategy,
            session_id=request.session_id, attachment_ids=request.attachments,
        )
        with self.database.transaction() as connection:
            self.runs.insert(connection, run)

        started = time.monotonic()
        try:
            decision = router.route(request.task)
            if (
                decision.strategy != request.routing_strategy
                or (decision.rerank_result is not None and self.rerank_model_key is None)
            ):
                decision = None
        except Exception:
            decision = None
        trace = self._trace(run, decision, started)
        with self.database.transaction() as connection:
            self.traces.insert(connection, trace)

        if trace.status == "rejected":
            ended = self.transitions.finish(
                run.run_id, "rejected", at=datetime.now(timezone.utc),
                error_code="NO_SUITABLE_AGENT",
            )
            return self._response(ended, trace, decision)
        if trace.status == "failed":
            ended = self.transitions.finish(
                run.run_id, "failed", at=datetime.now(timezone.utc),
                error_code=trace.error_code,
            )
            return self._response(ended, trace, decision)

        selected = decision.selected_agent
        current = self.versions.get_current(selected.agent_id)
        if (
            current is None or current.status != "active"
            or current.snapshot.version != selected.version
        ):
            return self._pre_dispatch_failure(
                run, trace, decision, "SELECTED_AGENT_UNAVAILABLE"
            )
        try:
            workflow = self.validator.validate(current.snapshot.runtime_ref)
        except Exception:
            return self._pre_dispatch_failure(run, trace, decision, "RUNTIME_REF_INVALID")

        execution = AgentRun(
            agent_run_id=uuid4(), run_id=run.run_id,
            agent_id=current.snapshot.id, agent_version=current.snapshot.version,
            runtime_ref=current.snapshot.runtime_ref, session_id=request.session_id,
            workflow_id=workflow.graph_id, workflow_revision=workflow.revision,
            node_id=workflow.agent_node_id, status="running",
            started_at=datetime.now(timezone.utc),
        )
        running = self.transitions.start(run.run_id, execution)
        context = DispatchContext(
            run_id=run.run_id, agent_run_id=execution.agent_run_id,
            session_id=request.session_id, task=request.task,
            attachment_ids=request.attachments, agent=current.snapshot, workflow=workflow,
        )
        try:
            await self.dispatcher.dispatch(context)
        except Exception:
            finished = datetime.now(timezone.utc)
            failed = self.transitions.finish(
                run.run_id, "failed", at=finished,
                latency_ms=(finished - execution.started_at).total_seconds() * 1000,
                error_code="EXECUTION_DISPATCH_FAILED",
            )
            return self._response(failed, trace, decision)
        return self._response(running, trace, decision)

    def _validate_session_and_attachments(self, request: TaskSubmissionInput) -> None:
        session_id = request.session_id
        if (
            session_id not in self.manager.active_connections
            and not self.manager.session_store.has_session(session_id)
        ):
            raise TaskSubmissionError("SESSION_NOT_FOUND")
        if not request.attachments:
            return
        store = self.manager.attachment_service.get_attachment_store(session_id)
        root = store.root.resolve()
        for attachment_id in request.attachments:
            record = store.get(attachment_id)
            if record is None or record.ref.local_path is None:
                raise TaskSubmissionError("INVALID_ATTACHMENT")
            if record.extra.get("session_id") not in (None, session_id):
                raise TaskSubmissionError("INVALID_ATTACHMENT")
            try:
                path = Path(record.ref.local_path).resolve(strict=True)
                if not path.is_file() or not path.is_relative_to(root):
                    raise TaskSubmissionError("INVALID_ATTACHMENT")
            except (OSError, RuntimeError):
                raise TaskSubmissionError("INVALID_ATTACHMENT") from None

    def _trace(
        self, run: TaskRun, decision: RoutingResult | None, started: float
    ) -> RoutingTrace:
        candidates = decision.candidates if decision is not None else ()
        refs = tuple(
            TraceAgent(agent_id=candidate.agent_id, version=candidate.version)
            for candidate in candidates
        )
        rerank = None
        if decision is not None and decision.rerank_result is not None:
            by_id = {ref.agent_id: ref for ref in refs}
            rerank = TraceRerank(
                input_candidates=refs,
                ordered_candidates=tuple(
                    by_id[agent_id]
                    for agent_id in decision.rerank_result.ordered_candidate_ids
                ),
                model_key=self.rerank_model_key,
                reason_code=decision.rerank_result.reason_code,
            )
        status = decision.status if decision is not None else "failed"
        code = decision.error_code if decision is not None else None
        if status == "failed" and (code is None or _ERROR_CODE.fullmatch(code) is None):
            code = "ROUTING_INFRASTRUCTURE_ERROR"
        return RoutingTrace(
            trace_id=uuid4(), run_id=run.run_id, strategy=run.strategy,
            status=status, created_at=datetime.now(timezone.utc),
            routing_latency_ms=routing_elapsed_ms(started),
            eligible_agents=refs,
            candidates=tuple(
                TraceCandidate(agent=ref, raw_similarity=candidate.raw_similarity)
                for ref, candidate in zip(refs, candidates, strict=True)
            ),
            threshold_value=decision.threshold.value if decision is not None else None,
            threshold_source=decision.threshold.source if decision is not None else None,
            embedding_model_key=self.embedding_model_key,
            selected_agent=(
                TraceAgent(
                    agent_id=decision.selected_agent.agent_id,
                    version=decision.selected_agent.version,
                ) if decision is not None and decision.selected_agent is not None else None
            ),
            rejection_reason="NO_SUITABLE_AGENT" if status == "rejected" else None,
            error_code=code if status == "failed" else None,
            rerank=rerank,
        )

    def _pre_dispatch_failure(
        self, run: TaskRun, trace: RoutingTrace, decision: RoutingResult, code: str
    ) -> TaskSubmissionResponse:
        failed = self.transitions.finish(
            run.run_id, "failed", at=datetime.now(timezone.utc), error_code=code,
        )
        return self._response(failed, trace, decision)

    @staticmethod
    def _response(
        run: TaskRun, trace: RoutingTrace, decision: RoutingResult | None
    ) -> TaskSubmissionResponse:
        names = {
            candidate.agent_id: candidate.public_metadata.name
            for candidate in (decision.candidates if decision is not None else ())
        }
        return TaskSubmissionResponse(
            run_id=run.run_id, session_id=run.session_id,
            status=run.status, routing_status=trace.status,
            selected_agent=(
                SelectedAgentSummary(
                    id=trace.selected_agent.agent_id,
                    version=trace.selected_agent.version,
                    name=names.get(trace.selected_agent.agent_id, "Selected Agent"),
                ) if trace.selected_agent is not None else None
            ),
            candidates=tuple(
                CandidateSummary(
                    id=item.agent.agent_id, version=item.agent.version,
                    name=names.get(item.agent.agent_id, "Candidate Agent"),
                    raw_similarity=item.raw_similarity,
                ) for item in trace.candidates
            ),
            error_code=run.error_code,
        )
