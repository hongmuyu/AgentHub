"""Launch selected AgentHub workflows through the existing Web service."""

import asyncio
from datetime import datetime, timezone

from runtime.node.agent_outcome import AgentOutcomeRecorder
from server.services.session_store import SessionStatus
from server.services.websocket_manager import WebSocketManager

from .agent_runs import AgentRunRepository
from .database import AgentHubDatabase
from .run_transitions import InvalidRunTransition, RunTransitionService
from .task_service import DispatchContext, TaskDispatchError
from .thin_workflow import ThinWorkflowValidator
from .versions import AgentVersionRepository


class AgentHubWorkflowDispatcher:
    """Revalidate one selected workflow, then use the normal Web execution chain."""

    def __init__(
        self,
        database: AgentHubDatabase,
        manager: WebSocketManager,
        validator: ThinWorkflowValidator,
    ) -> None:
        self.manager = manager
        self.validator = validator
        self.versions = AgentVersionRepository(database)
        self.executions = AgentRunRepository(database)
        self.transitions = RunTransitionService(database)
        self._in_flight: set[asyncio.Task] = set()

    async def dispatch(self, context: DispatchContext) -> None:
        current = self.versions.get_current(context.agent.id)
        if current is None or current.status != "active" or current.snapshot != context.agent:
            raise TaskDispatchError("SELECTED_AGENT_UNAVAILABLE")

        try:
            workflow = self.validator.validate(context.agent.runtime_ref)
            web_path = self.manager.workflow_run_service._resolve_yaml_path(workflow.path.name)
            if (
                workflow != context.workflow
                or web_path.resolve(strict=True) != workflow.path.resolve(strict=True)
            ):
                raise ValueError("workflow target changed")
        except Exception:
            raise TaskDispatchError("RUNTIME_REF_INVALID") from None

        recorder = AgentOutcomeRecorder(run_id=context.run_id, node_id=workflow.agent_node_id)
        task = asyncio.create_task(self._execute(context, workflow.path.name, recorder))
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)

    async def _execute(
        self, context: DispatchContext, yaml_file: str, recorder: AgentOutcomeRecorder,
    ) -> None:
        try:
            await self.manager.workflow_run_service.start_workflow(
                context.session_id, yaml_file, context.task, self.manager,
                attachments=list(context.attachment_ids), outcome_recorder=recorder,
            )
        except Exception:
            self._record_web_error(context)
            return

        session = self.manager.session_store.get_session(context.session_id)
        if session is None or session.status == SessionStatus.ERROR:
            self._record_web_error(context)
        elif session.status == SessionStatus.COMPLETED:
            self._record_completion(context, recorder)

    def _record_completion(
        self, context: DispatchContext, recorder: AgentOutcomeRecorder,
    ) -> None:
        session = self.manager.session_store.get_session(context.session_id)
        if (
            session is None or session.status != SessionStatus.COMPLETED
            or session.cancel_event.is_set()
        ):
            return

        recorded = recorder.read()
        outcome = recorded.outcome
        if recorded.status == "missing" and outcome is None:
            status, error_code = "failed", "EXECUTION_OUTCOME_MISSING"
        elif (
            recorded.status != "recorded" or outcome is None
            or outcome.run_id != context.run_id
            or outcome.node_id != context.workflow.agent_node_id
        ):
            status, error_code = "failed", "EXECUTION_OUTCOME_INVALID"
        elif outcome.state == "failed":
            status, error_code = "failed", outcome.error_code
        else:
            status, error_code = "success", None

        execution = self.executions.get_by_run_id(context.run_id)
        if execution is None:
            raise RuntimeError("selected AgentRun is missing")
        if status == "success" and execution.native_status in ("error", "cancelled"):
            return
        finished = datetime.now(timezone.utc)
        try:
            self.transitions.finish(
                context.run_id, status, at=finished,
                latency_ms=max(0.0, (finished - execution.started_at).total_seconds() * 1000),
                native_status=session.status.value, error_code=error_code,
                workflow_completed=status == "success",
                agent_outcome="succeeded" if status == "success" else None,
            )
        except InvalidRunTransition:
            # A previously committed failure or cancellation wins the race.
            pass

    def _record_web_error(self, context: DispatchContext) -> None:
        execution = self.executions.get_by_run_id(context.run_id)
        if execution is None:
            raise RuntimeError("selected AgentRun is missing")
        session = self.manager.session_store.get_session(context.session_id)
        native_status = "error" if session is not None and session.status == SessionStatus.ERROR else None
        finished = datetime.now(timezone.utc)
        self.transitions.finish(
            context.run_id, "failed", at=finished,
            latency_ms=max(0.0, (finished - execution.started_at).total_seconds() * 1000),
            native_status=native_status, error_code="WORKFLOW_EXECUTION_ERROR",
        )
