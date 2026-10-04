"""Semantic Agent selection and confidence gating before runtime execution."""

import math
from dataclasses import dataclass
from typing import Literal, Protocol

from .discovery import DiscoveryCandidate
from .embeddings import EmbeddingValidationError
from .runtime_resolver import RuntimeReferenceError


class DiscoverySource(Protocol):
    def discover(self, task: str, *, k: int) -> tuple[DiscoveryCandidate, ...]: ...


@dataclass(frozen=True)
class CalibratedThreshold:
    value: float
    source: str

    def __post_init__(self) -> None:
        if (
            type(self.value) not in (int, float)
            or not -1 <= self.value <= 1
            or not math.isfinite(self.value)
            or not isinstance(self.source, str)
            or not self.source.strip()
        ):
            raise ValueError("threshold requires a finite cosine value and calibration source")


@dataclass(frozen=True)
class RoutingResult:
    status: Literal["selected", "rejected", "failed"]
    candidates: tuple[DiscoveryCandidate, ...]
    threshold: CalibratedThreshold
    selected_agent: DiscoveryCandidate | None = None
    reason_code: str | None = None
    error_code: str | None = None
    strategy: Literal["semantic"] = "semantic"


class SemanticRouter:
    def __init__(
        self, discovery: DiscoverySource, *, threshold: CalibratedThreshold, top_k: int
    ) -> None:
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k must be positive")
        self.discovery = discovery
        self.threshold = threshold
        self.top_k = top_k

    def route(self, task: str) -> RoutingResult:
        try:
            candidates = tuple(self.discovery.discover(task, k=self.top_k))
        except Exception as error:
            if isinstance(error, (EmbeddingValidationError, RuntimeReferenceError)):
                error_code = error.code
            else:
                error_code = "ROUTING_INFRASTRUCTURE_ERROR"
            return RoutingResult(
                status="failed", candidates=(), threshold=self.threshold,
                error_code=error_code,
            )

        if not candidates or candidates[0].raw_similarity < self.threshold.value:
            return RoutingResult(
                status="rejected", candidates=candidates, threshold=self.threshold,
                reason_code="NO_SUITABLE_AGENT",
            )
        return RoutingResult(
            status="selected", candidates=candidates, threshold=self.threshold,
            selected_agent=candidates[0],
        )
