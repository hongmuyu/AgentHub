"""Structured Agent execution facts scoped to one run and target node."""

import re
from dataclasses import dataclass
from threading import Lock
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_NODE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_ERROR_CATEGORY = re.compile(r"[a-z][a-z0-9_]{0,63}")


class AgentCallSummary(BaseModel):
    """Known call counts only; unknown counts remain null."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    provider_calls: int | None = Field(default=None, strict=True, ge=0)
    tool_calls: int | None = Field(default=None, strict=True, ge=0)


class AgentExecutionOutcome(BaseModel):
    """One structured success or failure, independent of visible Messages."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    run_id: UUID
    node_id: str
    state: Literal["succeeded", "failed"]
    error_code: str | None = None
    error_category: str | None = None
    call_summary: AgentCallSummary | None = None

    @field_validator("run_id")
    @classmethod
    def valid_run_id(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("run_id must not be the nil UUID")
        return value

    @field_validator("node_id")
    @classmethod
    def valid_node_id(cls, value: str) -> str:
        if _NODE_ID.fullmatch(value) is None:
            raise ValueError("node_id must be a bounded logical identifier")
        return value

    @field_validator("error_code")
    @classmethod
    def valid_error_code(cls, value: str | None) -> str | None:
        if value is not None and _ERROR_CODE.fullmatch(value) is None:
            raise ValueError("error_code must be a safe reason code")
        return value

    @field_validator("error_category")
    @classmethod
    def valid_error_category(cls, value: str | None) -> str | None:
        if value is not None and _ERROR_CATEGORY.fullmatch(value) is None:
            raise ValueError("error_category must be a safe category key")
        return value

    @model_validator(mode="after")
    def valid_state_fields(self) -> "AgentExecutionOutcome":
        if self.state == "failed" and (self.error_code is None or self.error_category is None):
            raise ValueError("failed outcome requires code and category")
        if self.state == "succeeded" and (self.error_code is not None or self.error_category is not None):
            raise ValueError("succeeded outcome cannot include error fields")
        return self


@dataclass(frozen=True)
class OutcomeRead:
    status: Literal["recorded", "missing", "invalid"]
    outcome: AgentExecutionOutcome | None
    error_code: str | None


class AgentOutcomeRecorder:
    """Accept one outcome per run/node; conflicts remain invalid."""

    def __init__(self, *, run_id: UUID, node_id: str) -> None:
        AgentExecutionOutcome.valid_run_id(run_id)
        AgentExecutionOutcome.valid_node_id(node_id)
        self.run_id = run_id
        self.node_id = node_id
        self._lock = Lock()
        self._outcome: AgentExecutionOutcome | None = None
        self._invalid = False

    def record(self, outcome: AgentExecutionOutcome) -> None:
        if not isinstance(outcome, AgentExecutionOutcome):
            raise TypeError("record requires AgentExecutionOutcome")
        if outcome.run_id != self.run_id or outcome.node_id != self.node_id:
            raise ValueError("outcome target mismatch")
        with self._lock:
            if self._invalid:
                return
            if self._outcome is None:
                self._outcome = outcome
            elif self._outcome != outcome:
                self._outcome = None
                self._invalid = True

    def read(self) -> OutcomeRead:
        with self._lock:
            if self._invalid:
                return OutcomeRead("invalid", None, "EXECUTION_OUTCOME_INVALID")
            if self._outcome is None:
                return OutcomeRead("missing", None, "EXECUTION_OUTCOME_MISSING")
            return OutcomeRead("recorded", self._outcome, None)
