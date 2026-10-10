"""Persistent business TaskRuns, separate from ChatDev Web sessions."""

import json
import re
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .database import AgentHubDatabase
from .metadata import _CREDENTIAL_PATTERN


_REFERENCE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


class TaskRun(BaseModel):
    """One accepted platform submission; session is only an optional correlation."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    run_id: UUID
    task: str
    attachment_ids: tuple[str, ...] = ()
    strategy: Literal["semantic", "semantic_llm"]
    status: Literal["pending", "running", "success", "failed", "rejected", "cancelled"]
    created_at: datetime
    session_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    routing_trace_id: UUID | None = None
    agent_run_id: UUID | None = None
    error_code: str | None = None
    error_message: str | None = Field(default=None, max_length=256)
    result_ref: str | None = Field(default=None, max_length=512)
    result_summary: str | None = Field(default=None, max_length=512)

    @field_validator("run_id", "routing_trace_id", "agent_run_id")
    @classmethod
    def valid_id(cls, value: UUID | None) -> UUID | None:
        if value is not None and value.int == 0:
            raise ValueError("IDs must not be the nil UUID")
        return value

    @field_validator("task")
    @classmethod
    def valid_task(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task must not be blank")
        return value

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("session_id must not be blank")
        return value

    @field_validator("attachment_ids")
    @classmethod
    def valid_attachment_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(_REFERENCE_ID.fullmatch(value) is None for value in values):
            raise ValueError("attachment_ids must contain only attachment references")
        return values

    @field_validator("error_code")
    @classmethod
    def valid_error_code(cls, value: str | None) -> str | None:
        if value is not None and _ERROR_CODE.fullmatch(value) is None:
            raise ValueError("error_code must be a safe reason code")
        return value

    @field_validator("error_message", "result_ref", "result_summary")
    @classmethod
    def valid_short_text(cls, value: str | None, info) -> str | None:
        if value is not None and (not value.strip() or "\n" in value or "\r" in value):
            raise ValueError("reference and summary fields must be short single-line text")
        if value is not None and info.field_name == "error_message" and _CREDENTIAL_PATTERN.search(value):
            raise ValueError("error_message must not contain credential-like text")
        return value

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a UTC offset")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def valid_time_order(self) -> "TaskRun":
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at must not precede created_at")
        if self.finished_at is not None and self.finished_at < (self.started_at or self.created_at):
            raise ValueError("finished_at must not precede start")
        return self

    @classmethod
    def new(
        cls, *, task: str, strategy: Literal["semantic", "semantic_llm"],
        session_id: str | None = None, attachment_ids: tuple[str, ...] = (),
    ) -> "TaskRun":
        return cls(
            run_id=uuid4(), task=task, strategy=strategy, status="pending",
            created_at=datetime.now(timezone.utc), session_id=session_id,
            attachment_ids=attachment_ids,
        )


class TaskRunRepository:
    """Insert and retrieve TaskRuns; status transitions belong to a later task."""

    def __init__(self, database: AgentHubDatabase) -> None:
        self.database = database
        self.initialize()

    def initialize(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agenthub_task_runs (
                    run_id TEXT PRIMARY KEY CHECK (length(run_id) = 36),
                    task TEXT NOT NULL CHECK (length(trim(task)) > 0),
                    attachment_ids_json TEXT NOT NULL,
                    strategy TEXT NOT NULL CHECK (strategy IN ('semantic', 'semantic_llm')),
                    status TEXT NOT NULL CHECK (status IN (
                        'pending', 'running', 'success', 'failed', 'rejected', 'cancelled'
                    )),
                    created_at TEXT NOT NULL,
                    session_id TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    routing_trace_id TEXT,
                    agent_run_id TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    result_ref TEXT,
                    result_summary TEXT
                )
                """
            )

    def insert(self, connection: sqlite3.Connection, run: TaskRun) -> None:
        if not connection.in_transaction:
            raise RuntimeError("TaskRun writes require an explicit transaction")
        connection.execute(
            """
            INSERT INTO agenthub_task_runs (
                run_id, task, attachment_ids_json, strategy, status, created_at,
                session_id, started_at, finished_at, routing_trace_id, agent_run_id,
                error_code, error_message, result_ref, result_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run.run_id), run.task, json.dumps(run.attachment_ids),
                run.strategy, run.status, run.created_at.isoformat(), run.session_id,
                run.started_at.isoformat() if run.started_at else None,
                run.finished_at.isoformat() if run.finished_at else None,
                str(run.routing_trace_id) if run.routing_trace_id else None,
                str(run.agent_run_id) if run.agent_run_id else None,
                run.error_code, run.error_message, run.result_ref, run.result_summary,
            ),
        )

    def get(self, run_id: UUID, *, connection: sqlite3.Connection | None = None) -> TaskRun | None:
        if not isinstance(run_id, UUID) or run_id.int == 0:
            raise ValueError("run_id must be a non-nil UUID")
        with nullcontext(connection) if connection is not None else self.database.connection() as db:
            row = db.execute(
                """
                SELECT run_id, task, attachment_ids_json, strategy, status, created_at,
                       session_id, started_at, finished_at, routing_trace_id, agent_run_id,
                       error_code, error_message, result_ref, result_summary
                FROM agenthub_task_runs WHERE run_id = ?
                """,
                (str(run_id),),
            ).fetchone()
        if row is None:
            return None
        fields = (
            "run_id", "task", "attachment_ids", "strategy", "status", "created_at",
            "session_id", "started_at", "finished_at", "routing_trace_id", "agent_run_id",
            "error_code", "error_message", "result_ref", "result_summary",
        )
        values = list(row)
        values[2] = json.loads(values[2])
        return TaskRun.model_validate(dict(zip(fields, values)))
