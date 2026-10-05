"""AgentHub natural-language task submission without changing legacy execution."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

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
