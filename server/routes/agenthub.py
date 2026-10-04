"""Trusted internal HTTP entry points for the AgentHub Registry."""

import sqlite3
from datetime import datetime
from typing import Callable, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from server.services.agenthub.embeddings import EmbeddingValidationError
from server.services.agenthub.metadata import AgentMetadata, AgentMetadataInput
from server.services.agenthub.registry import AgentIndexNotReadyError, AgentRegistry
from server.services.agenthub.runtime_resolver import RuntimeReferenceError
from server.services.agenthub.thin_workflow import WorkflowValidationError


router = APIRouter(prefix="/api/agenthub/agents", tags=["agenthub"])


class AgentPublic(BaseModel):
    id: UUID
    name: str
    description: str
    capabilities: tuple[str, ...]
    tools: tuple[str, ...]
    tags: tuple[str, ...]
    version: int
    status: Literal["active", "disabled"]
    runtime_ref: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_metadata(cls, agent: AgentMetadata) -> "AgentPublic":
        return cls(
            **agent.snapshot.model_dump(), status=agent.status,
            created_at=agent.created_at, updated_at=agent.updated_at,
        )


class AgentListResponse(BaseModel):
    agents: tuple[AgentPublic, ...]
    limit: int
    offset: int


def _error(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code})


def _valid_id(agent_id: UUID) -> UUID:
    if agent_id.int == 0:
        raise _error(422, "INVALID_AGENT_ID")
    return agent_id


def get_registry(request: Request) -> AgentRegistry:
    registry = getattr(request.app.state, "agenthub_registry", None)
    if not isinstance(registry, AgentRegistry):
        raise _error(503, "REGISTRY_NOT_CONFIGURED")
    return registry


async def _metadata_body(request: Request) -> AgentMetadataInput:
    try:
        return AgentMetadataInput.model_validate(await request.json())
    except (ValueError, ValidationError):
        raise _error(422, "INVALID_AGENT_METADATA") from None


def _write(operation: Callable[[], AgentMetadata], *, enable: bool = False) -> AgentPublic:
    try:
        return AgentPublic.from_metadata(operation())
    except KeyError:
        raise _error(503, "EMBEDDING_NOT_READY") from None
    except LookupError:
        raise _error(404, "AGENT_NOT_FOUND") from None
    except RuntimeReferenceError as exc:
        raise _error(409 if enable else 422, exc.code) from None
    except WorkflowValidationError:
        raise _error(409 if enable else 422, "INVALID_WORKFLOW") from None
    except AgentIndexNotReadyError:
        raise _error(409, "INDEX_NOT_READY") from None
    except EmbeddingValidationError:
        raise _error(503, "EMBEDDING_NOT_READY") from None
    except sqlite3.DatabaseError:
        raise _error(503, "REGISTRY_WRITE_FAILED") from None
    except ValueError:
        raise _error(422, "INVALID_AGENT_METADATA") from None


@router.post("", response_model=AgentPublic, status_code=201)
async def register_agent(
    request: Request, registry: AgentRegistry = Depends(get_registry)
) -> AgentPublic:
    content = await _metadata_body(request)
    return _write(lambda: registry.register(content))


@router.get("", response_model=AgentListResponse)
def list_agents(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_disabled: bool = False,
    status: Literal["active", "disabled"] | None = None,
    q: str | None = None,
    registry: AgentRegistry = Depends(get_registry),
) -> AgentListResponse:
    try:
        agents = (
            registry.list(limit=limit, offset=offset, include_disabled=include_disabled, status=status)
            if q is None else registry.search(
                q, limit=limit, offset=offset,
                include_disabled=include_disabled, status=status,
            )
        )
    except ValueError:
        raise _error(422, "INVALID_LIST_QUERY") from None
    except sqlite3.DatabaseError:
        raise _error(503, "REGISTRY_READ_FAILED") from None
    return AgentListResponse(
        agents=tuple(AgentPublic.from_metadata(agent) for agent in agents),
        limit=limit, offset=offset,
    )


@router.get("/{agent_id}", response_model=AgentPublic)
def get_agent(agent_id: UUID, registry: AgentRegistry = Depends(get_registry)) -> AgentPublic:
    _valid_id(agent_id)
    try:
        agent = registry.get(agent_id)
    except sqlite3.DatabaseError:
        raise _error(503, "REGISTRY_READ_FAILED") from None
    if agent is None:
        raise _error(404, "AGENT_NOT_FOUND")
    return AgentPublic.from_metadata(agent)


@router.put("/{agent_id}", response_model=AgentPublic)
async def update_agent(
    agent_id: UUID, request: Request, registry: AgentRegistry = Depends(get_registry)
) -> AgentPublic:
    _valid_id(agent_id)
    content = await _metadata_body(request)
    return _write(lambda: registry.update(agent_id, content))


@router.post("/{agent_id}/enable", response_model=AgentPublic)
def enable_agent(
    agent_id: UUID, registry: AgentRegistry = Depends(get_registry)
) -> AgentPublic:
    _valid_id(agent_id)
    return _write(lambda: registry.enable(agent_id), enable=True)


@router.post("/{agent_id}/disable", response_model=AgentPublic)
def disable_agent(
    agent_id: UUID, registry: AgentRegistry = Depends(get_registry)
) -> AgentPublic:
    _valid_id(agent_id)
    return _write(lambda: registry.disable(agent_id))
