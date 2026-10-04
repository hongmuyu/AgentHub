"""One business Agent execution snapshot per TaskRun."""

import json
import re
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .database import AgentHubDatabase
from .metadata import AgentMetadataInput, _CREDENTIAL_PATTERN


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_RESULT_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,511}")
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


class TokenUsageSummary(BaseModel):
    """Nullable totals; missing usage is not the same as a known zero."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    total_tokens: int = Field(strict=True, ge=0)
    input_tokens: int | None = Field(default=None, strict=True, ge=0)
    output_tokens: int | None = Field(default=None, strict=True, ge=0)


class AgentRun(BaseModel):
    """Immutable execution identity with separately tracked business/native status."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    agent_run_id: UUID
    run_id: UUID
    agent_id: UUID
    agent_version: int = Field(strict=True, ge=1)
    runtime_ref: str
    session_id: str
    workflow_id: str
    workflow_revision: int = Field(strict=True, ge=1)
    node_id: str
    status: Literal["running", "success", "failed", "cancelled"]
    native_status: Literal[
        "idle", "running", "waiting_for_input", "completed", "error", "cancelled"
    ] | None = None
    started_at: datetime
    finished_at: datetime | None = None
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    token_usage: TokenUsageSummary | None = None
    result_ref: str | None = None
    result_summary: str | None = Field(default=None, max_length=512)
    error_code: str | None = None
    error_message: str | None = Field(default=None, max_length=256)
    native_error_code: str | None = None

    @field_validator("agent_run_id", "run_id", "agent_id")
    @classmethod
    def valid_id(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("IDs must not be the nil UUID")
        return value

    @field_validator("runtime_ref")
    @classmethod
    def valid_runtime_ref(cls, value: str) -> str:
        return AgentMetadataInput.validate_runtime_ref(value)

    @field_validator("session_id", "workflow_id", "node_id")
    @classmethod
    def valid_identifier(cls, value: str) -> str:
        if _IDENTIFIER.fullmatch(value) is None:
            raise ValueError("execution identifiers must be bounded logical keys")
        return value

    @field_validator("result_ref")
    @classmethod
    def valid_result_ref(cls, value: str | None) -> str | None:
        if value is not None and (
            _RESULT_REF.fullmatch(value) is None or _CREDENTIAL_PATTERN.search(value)
        ):
            raise ValueError("result_ref must be a safe logical reference")
        return value

    @field_validator("result_summary", "error_message")
    @classmethod
    def valid_short_text(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or "\n" in value or "\r" in value
            or _CREDENTIAL_PATTERN.search(value)
        ):
            raise ValueError("summary and error message must be safe short text")
        return value

    @field_validator("error_code", "native_error_code")
    @classmethod
    def valid_error_code(cls, value: str | None) -> str | None:
        if value is not None and _ERROR_CODE.fullmatch(value) is None:
            raise ValueError("error fields must be safe reason codes")
        return value

    @field_validator("started_at", "finished_at")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a UTC offset")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def valid_execution_fields(self) -> "AgentRun":
        if self.workflow_revision != int(self.runtime_ref.rsplit("/", 1)[1]):
            raise ValueError("workflow_revision must match runtime_ref")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if (self.finished_at is None) != (self.latency_ms is None):
            raise ValueError("finished_at and latency_ms must both be present or null")
        return self


class AgentRunRepository:
    """Store one selected Agent/version and workflow association per task."""

    def __init__(self, database: AgentHubDatabase) -> None:
        self.database = database
        self.initialize()

    def initialize(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agenthub_agent_runs (
                    agent_run_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE REFERENCES agenthub_task_runs(run_id)
                        ON DELETE RESTRICT,
                    agent_id TEXT NOT NULL,
                    agent_version INTEGER NOT NULL CHECK (agent_version >= 1),
                    runtime_ref TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workflow_revision INTEGER NOT NULL CHECK (workflow_revision >= 1),
                    node_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed', 'cancelled')),
                    native_status TEXT CHECK (native_status IN (
                        'idle', 'running', 'waiting_for_input', 'completed', 'error', 'cancelled'
                    )),
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    latency_ms REAL CHECK (latency_ms >= 0),
                    token_usage_json TEXT,
                    result_ref TEXT,
                    result_summary TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    native_error_code TEXT,
                    FOREIGN KEY (agent_id, agent_version)
                        REFERENCES agenthub_agent_versions(agent_id, version) ON DELETE RESTRICT
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS agenthub_agent_runs_snapshot_immutable
                BEFORE UPDATE OF agent_run_id, run_id, agent_id, agent_version,
                                 runtime_ref, session_id, workflow_id, workflow_revision,
                                 node_id, started_at ON agenthub_agent_runs
                BEGIN SELECT RAISE(ABORT, 'AgentRun snapshot is immutable'); END
                """
            )

    def insert(self, connection: sqlite3.Connection, execution: AgentRun) -> None:
        if not connection.in_transaction:
            raise RuntimeError("AgentRun writes require an explicit transaction")
        task = connection.execute(
            "SELECT status, session_id FROM agenthub_task_runs WHERE run_id = ?",
            (str(execution.run_id),),
        ).fetchone()
        if task is not None:
            if task[0] == "rejected":
                raise ValueError("rejected TaskRun cannot have an AgentRun")
            if task[1] is not None and task[1] != execution.session_id:
                raise ValueError("AgentRun session_id must match TaskRun session_id")
        version = connection.execute(
            """SELECT content_json FROM agenthub_agent_versions
               WHERE agent_id = ? AND version = ?""",
            (str(execution.agent_id), execution.agent_version),
        ).fetchone()
        if version is not None and json.loads(version[0])["runtime_ref"] != execution.runtime_ref:
            raise ValueError("AgentRun runtime_ref must match the Agent version snapshot")
        connection.execute(
            """
            INSERT INTO agenthub_agent_runs (
                agent_run_id, run_id, agent_id, agent_version, runtime_ref, session_id,
                workflow_id, workflow_revision, node_id, status, native_status,
                started_at, finished_at, latency_ms, token_usage_json, result_ref,
                result_summary, error_code, error_message, native_error_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(execution.agent_run_id), str(execution.run_id), str(execution.agent_id),
                execution.agent_version, execution.runtime_ref, execution.session_id,
                execution.workflow_id, execution.workflow_revision, execution.node_id,
                execution.status, execution.native_status, execution.started_at.isoformat(),
                execution.finished_at.isoformat() if execution.finished_at else None,
                execution.latency_ms,
                execution.token_usage.model_dump_json() if execution.token_usage else None,
                execution.result_ref, execution.result_summary, execution.error_code,
                execution.error_message, execution.native_error_code,
            ),
        )
        linked = connection.execute(
            """UPDATE agenthub_task_runs SET agent_run_id = ?
               WHERE run_id = ? AND agent_run_id IS NULL""",
            (str(execution.agent_run_id), str(execution.run_id)),
        )
        if linked.rowcount != 1:
            raise ValueError("TaskRun already has an AgentRun reference")

    def get_by_run_id(
        self, run_id: UUID, *, connection: sqlite3.Connection | None = None
    ) -> AgentRun | None:
        if not isinstance(run_id, UUID) or run_id.int == 0:
            raise ValueError("run_id must be a non-nil UUID")
        with nullcontext(connection) if connection is not None else self.database.connection() as db:
            row = db.execute(
                """
                SELECT agent_run_id, run_id, agent_id, agent_version, runtime_ref,
                       session_id, workflow_id, workflow_revision, node_id, status,
                       native_status, started_at, finished_at, latency_ms,
                       token_usage_json, result_ref, result_summary, error_code,
                       error_message, native_error_code
                FROM agenthub_agent_runs WHERE run_id = ?
                """,
                (str(run_id),),
            ).fetchone()
        if row is None:
            return None
        fields = (
            "agent_run_id", "run_id", "agent_id", "agent_version", "runtime_ref",
            "session_id", "workflow_id", "workflow_revision", "node_id", "status",
            "native_status", "started_at", "finished_at", "latency_ms", "token_usage",
            "result_ref", "result_summary", "error_code", "error_message", "native_error_code",
        )
        values = list(row)
        values[14] = json.loads(values[14]) if values[14] is not None else None
        return AgentRun.model_validate(dict(zip(fields, values)))
