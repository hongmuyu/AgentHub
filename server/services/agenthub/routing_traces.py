"""Durable final routing decisions without model prompts or runtime configuration."""

import re
import sqlite3
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .database import AgentHubDatabase
from .metadata import _CREDENTIAL_PATTERN


_SAFE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


def routing_elapsed_ms(started_monotonic: float) -> float:
    """Convert a monotonic elapsed duration from seconds to milliseconds."""
    elapsed = time.monotonic() - started_monotonic
    if elapsed < 0:
        raise ValueError("routing start must not be after the current monotonic time")
    return elapsed * 1000


class TraceAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    agent_id: UUID
    version: int = Field(strict=True, ge=1)

    @field_validator("agent_id")
    @classmethod
    def valid_id(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("agent_id must not be the nil UUID")
        return value


class TraceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    agent: TraceAgent
    raw_similarity: float = Field(ge=-1, le=1, allow_inf_nan=False)
    score_kind: Literal["cosine"] = "cosine"


class TraceRerank(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    input_candidates: tuple[TraceAgent, ...]
    ordered_candidates: tuple[TraceAgent, ...]
    model_key: str
    reason_code: str | None = None

    @field_validator("model_key")
    @classmethod
    def valid_model_key(cls, value: str) -> str:
        if _SAFE_KEY.fullmatch(value) is None or _CREDENTIAL_PATTERN.search(value):
            raise ValueError("model_key must be a safe configured key")
        return value

    @field_validator("reason_code")
    @classmethod
    def valid_reason_code(cls, value: str | None) -> str | None:
        if value is not None and _REASON_CODE.fullmatch(value) is None:
            raise ValueError("reason_code must be a safe code")
        return value

    @model_validator(mode="after")
    def valid_order(self) -> "TraceRerank":
        inputs = self.input_candidates
        outputs = self.ordered_candidates
        if not inputs or len(set(inputs)) != len(inputs):
            raise ValueError("rerank input must contain distinct candidates")
        if len(set(outputs)) != len(outputs) or any(agent not in inputs for agent in outputs):
            raise ValueError("rerank output must be a candidate subset without duplicates")
        if outputs and self.reason_code is None:
            raise ValueError("rerank output requires a reason code")
        return self


class RoutingTrace(BaseModel):
    """One final snapshot for a business TaskRun."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    trace_id: UUID
    run_id: UUID
    strategy: Literal["semantic", "semantic_llm"]
    status: Literal["selected", "rejected", "failed"]
    created_at: datetime
    routing_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    eligible_agents: tuple[TraceAgent, ...]
    candidates: tuple[TraceCandidate, ...]
    threshold_value: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    threshold_source: str | None = None
    embedding_model_key: str | None = None
    selected_agent: TraceAgent | None = None
    rejection_reason: str | None = None
    error_code: str | None = None
    rerank: TraceRerank | None = None

    @field_validator("trace_id", "run_id")
    @classmethod
    def valid_id(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("IDs must not be the nil UUID")
        return value

    @field_validator("created_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a UTC offset")
        return value.astimezone(timezone.utc)

    @field_validator("threshold_source", "embedding_model_key")
    @classmethod
    def valid_config_key(cls, value: str | None) -> str | None:
        if value is not None and (
            _SAFE_KEY.fullmatch(value) is None or _CREDENTIAL_PATTERN.search(value)
        ):
            raise ValueError("model and calibration keys must be safe configured keys")
        return value

    @field_validator("rejection_reason", "error_code")
    @classmethod
    def valid_reason_code(cls, value: str | None) -> str | None:
        if value is not None and _REASON_CODE.fullmatch(value) is None:
            raise ValueError("reason and error fields must be safe codes")
        return value

    @model_validator(mode="after")
    def valid_decision(self) -> "RoutingTrace":
        if (self.threshold_value is None) != (self.threshold_source is None):
            raise ValueError("threshold value and source must both be present or null")
        eligible = self.eligible_agents
        recalled = tuple(candidate.agent for candidate in self.candidates)
        if len(set(eligible)) != len(eligible) or len(set(recalled)) != len(recalled):
            raise ValueError("eligible and recalled Agents must be unique")
        if any(agent not in eligible for agent in recalled):
            raise ValueError("recalled Agents must be eligible")
        if self.selected_agent is not None and self.selected_agent not in recalled:
            raise ValueError("selected Agent must be a recalled candidate")
        if self.selected_agent is not None and self.rejection_reason is not None:
            raise ValueError("selected Agent and rejection reason are mutually exclusive")
        if self.status == "selected":
            if self.selected_agent is None or self.rejection_reason or self.error_code:
                raise ValueError("selected trace requires only a selected Agent")
        elif self.status == "rejected":
            if self.selected_agent is not None or self.rejection_reason is None or self.error_code:
                raise ValueError("rejected trace requires only a rejection reason")
        elif self.rejection_reason is not None or self.error_code is None:
            raise ValueError("failed trace requires an error code, not a rejection reason")
        if self.rerank is not None:
            if self.strategy != "semantic_llm" or self.status == "rejected":
                raise ValueError("rerank requires a non-rejected semantic_llm route")
            if self.rerank.input_candidates != recalled:
                raise ValueError("rerank input must match recalled candidate order")
        if self.selected_agent is not None:
            if self.strategy == "semantic" and self.selected_agent != recalled[0]:
                raise ValueError("semantic selection must be the first recalled candidate")
            if self.strategy == "semantic_llm":
                if self.rerank is None or not self.rerank.ordered_candidates:
                    raise ValueError("semantic_llm selection requires a rerank order")
                if self.selected_agent != self.rerank.ordered_candidates[0]:
                    raise ValueError("selected Agent must be first in rerank order")
        return self


class RoutingTraceRepository:
    """Store at most one final trace per TaskRun with idempotent duplicate writes."""

    def __init__(self, database: AgentHubDatabase) -> None:
        self.database = database
        self.initialize()

    def initialize(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agenthub_routing_traces (
                    trace_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE REFERENCES agenthub_task_runs(run_id)
                        ON DELETE RESTRICT,
                    strategy TEXT NOT NULL CHECK (strategy IN ('semantic', 'semantic_llm')),
                    status TEXT NOT NULL CHECK (status IN ('selected', 'rejected', 'failed')),
                    created_at TEXT NOT NULL,
                    routing_latency_ms REAL NOT NULL CHECK (routing_latency_ms >= 0),
                    payload_json TEXT NOT NULL
                )
                """
            )

    def insert(self, connection: sqlite3.Connection, trace: RoutingTrace) -> None:
        if not connection.in_transaction:
            raise RuntimeError("RoutingTrace writes require an explicit transaction")
        run = connection.execute(
            "SELECT strategy FROM agenthub_task_runs WHERE run_id = ?",
            (str(trace.run_id),),
        ).fetchone()
        if run is not None and run[0] != trace.strategy:
            raise ValueError("RoutingTrace strategy must match TaskRun strategy")
        existing = connection.execute(
            "SELECT payload_json FROM agenthub_routing_traces WHERE run_id = ?",
            (str(trace.run_id),),
        ).fetchone()
        if existing is not None:
            if RoutingTrace.model_validate_json(existing[0]) == trace:
                return
            raise ValueError("TaskRun already has a different final trace")
        connection.execute(
            """
            INSERT INTO agenthub_routing_traces
                (trace_id, run_id, strategy, status, created_at, routing_latency_ms, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(trace.trace_id), str(trace.run_id), trace.strategy, trace.status,
                trace.created_at.isoformat(), trace.routing_latency_ms,
                trace.model_dump_json(),
            ),
        )
        linked = connection.execute(
            """
            UPDATE agenthub_task_runs SET routing_trace_id = ?
            WHERE run_id = ? AND routing_trace_id IS NULL
            """,
            (str(trace.trace_id), str(trace.run_id)),
        )
        if linked.rowcount != 1:
            raise ValueError("TaskRun already has a final trace reference")

    def get_by_run_id(
        self, run_id: UUID, *, connection: sqlite3.Connection | None = None
    ) -> RoutingTrace | None:
        if not isinstance(run_id, UUID) or run_id.int == 0:
            raise ValueError("run_id must be a non-nil UUID")
        with nullcontext(connection) if connection is not None else self.database.connection() as db:
            row = db.execute(
                "SELECT payload_json FROM agenthub_routing_traces WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
        return RoutingTrace.model_validate_json(row[0]) if row is not None else None
