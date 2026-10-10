"""Business Agent metadata, separate from runtime AgentConfig."""

import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_TEXT_LIMITS = {"name": 80, "description": 1000, "capabilities": 200, "tools": 80, "tags": 80}
_RUNTIME_REF_LIMIT = 128
_RUNTIME_REF_PATTERN = re.compile(r"workflow://[a-z0-9][a-z0-9_-]*/[1-9][0-9]*")
_CREDENTIAL_PATTERN = re.compile(
    r"(?:\b(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|token|secret|password)\s*[:=]\s*\S+"
    r"|\bbearer\s+\S+|\bsk-[a-z0-9_-]{16,})",
    re.IGNORECASE,
)


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _validate_public_text(value: str, *, field: str) -> str:
    normalized = _normalize_text(value)
    if not normalized or len(normalized) > _TEXT_LIMITS[field]:
        raise ValueError(f"{field} must contain 1 to {_TEXT_LIMITS[field]} characters")
    if _CREDENTIAL_PATTERN.search(normalized):
        raise ValueError(f"{field} contains credential-like text")
    if field == "tools" and "://" in normalized:
        raise ValueError("tools must contain names, not URLs")
    return normalized


def _normalize_items(values: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _validate_public_text(value, field=field)
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            result.append(key if field == "tags" else normalized)
    if field == "capabilities" and not result:
        raise ValueError("capabilities must not be empty")
    return tuple(result)


class AgentMetadataInput(BaseModel):
    """Public, versioned metadata supplied at registration or update."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    name: str
    description: str
    capabilities: tuple[str, ...]
    tools: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    runtime_ref: str

    @field_validator("name", "description")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _validate_public_text(value, field=info.field_name)

    @field_validator("runtime_ref")
    @classmethod
    def validate_runtime_ref(cls, value: str) -> str:
        normalized = _normalize_text(value)
        if len(normalized) > _RUNTIME_REF_LIMIT or not _RUNTIME_REF_PATTERN.fullmatch(normalized):
            raise ValueError("runtime_ref must be a workflow://key/positive-revision reference")
        return normalized

    @field_validator("capabilities", "tools", "tags")
    @classmethod
    def normalize_items(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        return _normalize_items(value, field=info.field_name)


class AgentMetadataVersion(AgentMetadataInput):
    """Immutable content snapshot for one business Agent version."""

    id: UUID
    version: int = Field(strict=True, ge=1)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("id must not be the nil UUID")
        return value


class AgentMetadata(BaseModel):
    """Directory identity and mutable status around an immutable version."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    snapshot: AgentMetadataVersion
    status: Literal["active", "disabled"]
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a UTC offset")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> "AgentMetadata":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self

    @classmethod
    def register(cls, content: AgentMetadataInput) -> "AgentMetadata":
        now = datetime.now(timezone.utc)
        snapshot = AgentMetadataVersion(**content.model_dump(), id=uuid4(), version=1)
        return cls(snapshot=snapshot, status="active", created_at=now, updated_at=now)

    def update_content(self, content: AgentMetadataInput) -> "AgentMetadata":
        snapshot = AgentMetadataVersion(
            **content.model_dump(), id=self.snapshot.id, version=self.snapshot.version + 1
        )
        return AgentMetadata(
            snapshot=snapshot,
            status=self.status,
            created_at=self.created_at,
            updated_at=max(datetime.now(timezone.utc), self.updated_at),
        )

    def set_status(self, status: Literal["active", "disabled"]) -> "AgentMetadata":
        return AgentMetadata(
            snapshot=self.snapshot,
            status=status,
            created_at=self.created_at,
            updated_at=max(datetime.now(timezone.utc), self.updated_at + timedelta(microseconds=1)),
        )
