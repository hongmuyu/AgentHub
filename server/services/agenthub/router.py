"""Semantic Agent selection and confidence gating before runtime execution."""

import math
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from .discovery import DiscoveryCandidate
from .embeddings import EmbeddingValidationError
from .reranker import RerankAdapter, RerankError, RerankResult
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
    strategy: Literal["semantic", "semantic_llm"] = "semantic"
    rerank_result: RerankResult | None = None


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


class SemanticLLMRouter:
    """Apply candidate-only reranking after the shared semantic gate."""

    def __init__(self, semantic_router: SemanticRouter, reranker: RerankAdapter) -> None:
        self.semantic_router = semantic_router
        self.reranker = reranker

    def route(self, task: str) -> RoutingResult:
        semantic = self.semantic_router.route(task)
        if semantic.status != "selected":
            return replace(semantic, strategy="semantic_llm")

        try:
            reranked = self.reranker.rerank(task, semantic.candidates)
        except RerankError as error:
            error_code = error.code
        except Exception:
            error_code = "RERANK_SERVICE_ERROR"
        else:
            by_id = {candidate.agent_id: candidate for candidate in semantic.candidates}
            ordered_ids = reranked.ordered_candidate_ids
            if (
                ordered_ids
                and len(set(ordered_ids)) == len(ordered_ids)
                and all(agent_id in by_id for agent_id in ordered_ids)
            ):
                return replace(
                    semantic, strategy="semantic_llm",
                    selected_agent=by_id[ordered_ids[0]], rerank_result=reranked,
                )
            error_code = "RERANK_INVALID_RESPONSE"

        return replace(
            semantic, status="failed", strategy="semantic_llm",
            selected_agent=None, error_code=error_code,
        )
