"""AgentHub natural-language task submission without changing legacy execution."""

import sqlite3
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from server.services.agenthub.run_query import RunQueryDataError, RunQueryResponse, RunQueryService
from server.services.agenthub.task_service import (
    TaskSubmissionError, TaskSubmissionInput, TaskSubmissionResponse,
    TaskSubmissionService,
)


router = APIRouter(prefix="/api/agenthub/tasks", tags=["agenthub"])


def get_task_service(request: Request) -> TaskSubmissionService:
    service = getattr(request.app.state, "agenthub_task_service", None)
    if not isinstance(service, TaskSubmissionService):
        raise HTTPException(
            status_code=503, detail={"code": "TASK_SERVICE_NOT_CONFIGURED"}
        )
    return service


def get_run_query_service(request: Request) -> RunQueryService:
    service = getattr(request.app.state, "agenthub_run_query_service", None)
    if not isinstance(service, RunQueryService):
        raise HTTPException(status_code=503, detail={"code": "RUN_QUERY_NOT_CONFIGURED"})
    return service


@router.post("", response_model=TaskSubmissionResponse, status_code=202)
async def submit_task(
    request: Request, service: TaskSubmissionService = Depends(get_task_service)
) -> TaskSubmissionResponse:
    try:
        content = TaskSubmissionInput.model_validate(await request.json())
    except (ValueError, ValidationError):
        raise HTTPException(status_code=422, detail={"code": "INVALID_TASK_REQUEST"}) from None
    try:
        return await service.submit(content)
    except TaskSubmissionError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code}) from None
    except sqlite3.DatabaseError:
        raise HTTPException(status_code=503, detail={"code": "TASK_STORAGE_FAILED"}) from None


@router.get("/{run_id}", response_model=RunQueryResponse)
def get_task_run(
    run_id: str, service: RunQueryService = Depends(get_run_query_service)
) -> RunQueryResponse:
    try:
        parsed_id = UUID(run_id)
        if parsed_id.int == 0:
            raise ValueError("nil UUID")
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "INVALID_RUN_ID"}) from None
    try:
        response = service.get(parsed_id)
    except (RunQueryDataError, ValidationError, ValueError):
        raise HTTPException(status_code=503, detail={"code": "RUN_DATA_INVALID"}) from None
    except sqlite3.DatabaseError:
        raise HTTPException(status_code=503, detail={"code": "RUN_READ_FAILED"}) from None
    if response is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND"})
    return response
