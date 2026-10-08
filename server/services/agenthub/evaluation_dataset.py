"""Versioned routing labels bound to one synthetic or recorded catalog snapshot.

The snapshot records each Agent identity, version and status; the curator assigns
its snapshot ID. Attachment references are logical fixture keys declared in the
dataset, not file paths or attachment contents. A later runner supplies the
matching metadata/fixtures and verifies its execution configuration separately.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator, model_validator


_KEY = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*$", min_length=1, max_length=80)]
_FIXTURE_REF = Annotated[
    str, StringConstraints(pattern=r"^fixture://[a-z0-9][a-z0-9_-]*$", max_length=128),
]


class CatalogAgentRef(BaseModel):
    """One identity/version and status at the dataset's catalog snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    agent_id: UUID
    version: int = Field(strict=True, ge=1)
    status: Literal["active", "disabled"]

    @field_validator("agent_id")
    @classmethod
    def non_nil_id(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("agent_id must not be nil")
        return value


class RoutingCase(BaseModel):
    """One annotated task; expected IDs form an unordered acceptable set."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    case_id: _KEY
    task_text: str
    attachment_fixture_ref: _FIXTURE_REF | None = None
    expected_agent_ids: tuple[UUID, ...]
    should_reject: StrictBool
    category: Literal["clear", "ambiguous", "no_match"]
    annotation_reason: str
    dataset_version: _KEY
    split: Literal["calibration", "test"]

    @field_validator("task_text", "annotation_reason")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > 1000:
            raise ValueError("annotation text must contain 1 to 1000 characters")
        return normalized

    @field_validator("expected_agent_ids")
    @classmethod
    def unique_expected_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if any(agent_id.int == 0 for agent_id in value) or len(value) != len(set(value)):
            raise ValueError("expected_agent_ids must contain distinct non-nil identities")
        return tuple(sorted(value, key=str))

    @model_validator(mode="after")
    def consistent_label(self) -> "RoutingCase":
        count = len(self.expected_agent_ids)
        if self.category == "no_match":
            valid = self.should_reject and count == 0
        elif self.category == "clear":
            valid = not self.should_reject and count == 1
        else:
            valid = not self.should_reject and count >= 2
        if not valid:
            raise ValueError("category, should_reject and expected_agent_ids disagree")
        return self


class RoutingDataset(BaseModel):
    """Case labels and fixture keys tied to one versioned catalog reference list."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    dataset_version: _KEY
    catalog_snapshot_id: _KEY
    catalog_agents: tuple[CatalogAgentRef, ...] = Field(min_length=1)
    attachment_fixture_refs: tuple[_FIXTURE_REF, ...] = ()
    cases: tuple[RoutingCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references_and_splits(self) -> "RoutingDataset":
        agents = {agent.agent_id: agent for agent in self.catalog_agents}
        if len(agents) != len(self.catalog_agents):
            raise ValueError("catalog snapshot contains duplicate Agent identities")
        fixtures = set(self.attachment_fixture_refs)
        if len(fixtures) != len(self.attachment_fixture_refs):
            raise ValueError("attachment fixture references must be unique")

        case_ids: set[str] = set()
        task_texts: set[str] = set()
        for case in self.cases:
            if case.dataset_version != self.dataset_version:
                raise ValueError("case dataset_version differs from dataset")
            if case.case_id in case_ids:
                raise ValueError("case_id appears in more than one case or split")
            case_ids.add(case.case_id)
            task_key = case.task_text.casefold()
            if task_key in task_texts:
                raise ValueError("task_text appears in more than one case or split")
            task_texts.add(task_key)
            if case.attachment_fixture_ref is not None and case.attachment_fixture_ref not in fixtures:
                raise ValueError("attachment fixture reference is not declared")
            if any(agents.get(agent_id) is None or agents[agent_id].status != "active"
                   for agent_id in case.expected_agent_ids):
                raise ValueError("expected Agent must be active in the catalog snapshot")
        return self
