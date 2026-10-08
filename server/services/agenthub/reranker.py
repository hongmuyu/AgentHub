"""Validated, injectable reranking of recalled Agent candidates."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .discovery import DiscoveryCandidate

_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_SAFE_TRANSPORT_CODES = frozenset({
    "RERANK_CREDENTIAL_MISSING", "RERANK_TIMEOUT", "RERANK_SERVICE_ERROR",
    "RERANK_AUTH_FAILED", "RERANK_RATE_LIMITED", "RERANK_SERVICE_UNAVAILABLE",
    "RERANK_REQUEST_FAILED", "RERANK_INVALID_RESPONSE", "RERANK_TRUNCATED_RESPONSE",
})


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class RerankError(Exception):
    """A safe code without transport or model response details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RerankTransport(Protocol):
    def complete(self, request: dict[str, object]) -> str: ...


@dataclass(frozen=True)
class RerankResult:
    ordered_candidate_ids: tuple[UUID, ...]
    reason_code: str


class RerankAdapter:
    def __init__(self, transport: RerankTransport) -> None:
        self.transport = transport

    def rerank(
        self, task: str, candidates: Sequence[DiscoveryCandidate]
    ) -> RerankResult:
        ordered = tuple(candidates)
        candidate_ids = {str(candidate.agent_id): candidate.agent_id for candidate in ordered}
        if (
            not isinstance(task, str)
            or not task.strip()
            or not ordered
            or len(candidate_ids) != len(ordered)
        ):
            raise RerankError("RERANK_INVALID_REQUEST")

        request = {
            "task": task,
            "candidates": [
                {
                    "id": str(candidate.agent_id),
                    "name": candidate.public_metadata.name,
                    "description": candidate.public_metadata.description,
                    "capabilities": list(candidate.public_metadata.capabilities),
                    "tags": list(candidate.public_metadata.tags),
                }
                for candidate in ordered
            ],
        }
        try:
            response = self.transport.complete(request)
        except RerankError as exc:
            code = exc.code if exc.code in _SAFE_TRANSPORT_CODES else "RERANK_SERVICE_ERROR"
            raise RerankError(code) from None
        except TimeoutError:
            raise RerankError("RERANK_TIMEOUT") from None
        except Exception:  # noqa: BLE001 - transport details must not escape
            raise RerankError("RERANK_SERVICE_ERROR") from None

        if not isinstance(response, str):
            raise RerankError("RERANK_INVALID_RESPONSE")
        try:
            parsed = json.loads(response, object_pairs_hook=_unique_object)
        except (ValueError, TypeError):
            raise RerankError("RERANK_INVALID_RESPONSE") from None
        if not isinstance(parsed, dict) or set(parsed) != {"candidate_ids", "reason_code"}:
            raise RerankError("RERANK_INVALID_RESPONSE")
        ids, reason_code = parsed["candidate_ids"], parsed["reason_code"]
        if (
            not isinstance(ids, list)
            or not ids
            or not isinstance(reason_code, str)
            or _REASON_CODE.fullmatch(reason_code) is None
        ):
            raise RerankError("RERANK_INVALID_RESPONSE")

        seen: set[str] = set()
        result: list[UUID] = []
        for agent_id in ids:
            if not isinstance(agent_id, str):
                raise RerankError("RERANK_INVALID_RESPONSE")
            if agent_id not in candidate_ids:
                raise RerankError("RERANK_UNKNOWN_CANDIDATE")
            if agent_id in seen:
                raise RerankError("RERANK_DUPLICATE_CANDIDATE")
            seen.add(agent_id)
            result.append(candidate_ids[agent_id])
        return RerankResult(tuple(result), reason_code)
