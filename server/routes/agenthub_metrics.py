"""Read AgentHub business metrics without exposing task or execution content."""

import sqlite3
from datetime import datetime
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.metrics import MetricsDataError, MetricsResponse, MetricsService


router = APIRouter(prefix="/api/agenthub/metrics", tags=["agenthub"])


@lru_cache(maxsize=1)
def _default_service() -> MetricsService:
    return MetricsService(AgentHubDatabase())


def get_metrics_service(request: Request) -> MetricsService:
    configured = getattr(request.app.state, "agenthub_metrics_service", None)
    return configured if isinstance(configured, MetricsService) else _default_service()


@router.get("", response_model=MetricsResponse)
def get_metrics(
    start: str | None = None, end: str | None = None,
    service: MetricsService = Depends(get_metrics_service),
) -> MetricsResponse:
    try:
        parsed_start = datetime.fromisoformat(start) if start is not None else None
        parsed_end = datetime.fromisoformat(end) if end is not None else None
        if any(value is not None and value.utcoffset() is None for value in (parsed_start, parsed_end)):
            raise ValueError("timezone required")
        if parsed_start is not None and parsed_end is not None and parsed_start >= parsed_end:
            raise ValueError("invalid window")
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "INVALID_METRICS_WINDOW"}) from None
    try:
        return service.get(start=parsed_start, end=parsed_end)
    except (MetricsDataError, ValidationError, ValueError):
        raise HTTPException(status_code=503, detail={"code": "METRICS_DATA_INVALID"}) from None
    except sqlite3.DatabaseError:
        raise HTTPException(status_code=503, detail={"code": "METRICS_READ_FAILED"}) from None
