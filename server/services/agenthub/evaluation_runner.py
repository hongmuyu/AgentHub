"""Run labeled routing cases without starting an Agent workflow."""

import hashlib
import json
import platform
import sys
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .discovery import AgentDiscovery
from .embeddings import EmbeddingBackend
from .evaluation_dataset import CatalogAgentRef, RoutingDataset
from .registry import AgentRegistry
from .reranker import RerankAdapter
from .router import CalibratedThreshold, SemanticLLMRouter, SemanticRouter
from .routing_traces import TraceAgent, TraceCandidate, TraceRerank


class BenchmarkEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    python_version: str
    platform: str


class BenchmarkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_snapshot_id: str
    catalog_agents: tuple[CatalogAgentRef, ...]
    dataset_version: str
    split: Literal["calibration", "test"]
    embedding_model_key: str
    top_k: int = Field(gt=0)
    threshold_value: float = Field(ge=-1, le=1, allow_inf_nan=False)
    threshold_source: str
    strategy: Literal["semantic", "semantic_llm"]
    rerank_model_key: str | None = None
    environment: BenchmarkEnvironment


class BenchmarkCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_id: str
    case_id: str
    status: Literal["selected", "rejected", "infra_error"]
    semantic_candidates: tuple[TraceCandidate, ...]
    selected_agent: TraceAgent | None = None
    rejection_reason: str | None = None
    error_code: str | None = None
    rerank: TraceRerank | None = None
    routing_latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    timing_scope: Literal["query_to_decision", "query_not_started"]

    @model_validator(mode="after")
    def consistent_decision(self) -> "BenchmarkCaseResult":
        if self.status == "selected":
            valid = self.selected_agent is not None and not self.rejection_reason and not self.error_code
        elif self.status == "rejected":
            valid = self.selected_agent is None and self.rejection_reason is not None and not self.error_code
        else:
            valid = self.selected_agent is None and not self.rejection_reason and self.error_code is not None
        if not valid or (self.routing_latency_ms is None) != (self.timing_scope == "query_not_started"):
            raise ValueError("benchmark decision or timing fields disagree")
        return self


class BenchmarkRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_id: str
    config: BenchmarkConfig
    cases: tuple[BenchmarkCaseResult, ...]


class _TimedEmbeddingBackend:
    """Start the benchmark clock at the query embedding call."""

    def __init__(self, backend: EmbeddingBackend) -> None:
        self.backend = backend
        self.model_key = backend.model_key
        self.dimensions = backend.dimensions
        self.started_ns: int | None = None

    def embed(self, texts):
        self.started_ns = time.monotonic_ns()
        return self.backend.embed(texts)


def _catalog_refs(registry: AgentRegistry) -> set[tuple[object, int, str]]:
    return {
        (agent.snapshot.id, agent.snapshot.version, agent.status)
        for agent in registry.versions.list_current()
    }


def run_routing_benchmark(
    dataset: RoutingDataset,
    registry: AgentRegistry,
    *,
    strategy: Literal["semantic", "semantic_llm"],
    split: Literal["calibration", "test"],
    top_k: int,
    threshold: CalibratedThreshold,
    environment_label: str,
    reranker: RerankAdapter | None = None,
    rerank_model_key: str | None = None,
) -> BenchmarkRun:
    """Record Router decisions and measured query-to-decision latency for one split."""
    dataset = RoutingDataset.model_validate(dataset)
    cases = tuple(case for case in dataset.cases if case.split == split)
    if any(case.attachment_fixture_ref is not None for case in cases):
        raise ValueError("attachment fixtures are not supplied to the routing benchmark")
    if strategy == "semantic_llm":
        if reranker is None or not rerank_model_key:
            raise ValueError("semantic_llm requires a reranker and model key")
    elif strategy != "semantic" or reranker is not None or rerank_model_key is not None:
        raise ValueError("semantic strategy must not receive a reranker")

    expected_refs = {(ref.agent_id, ref.version, ref.status) for ref in dataset.catalog_agents}
    if _catalog_refs(registry) != expected_refs:
        raise ValueError("catalog snapshot differs from routing dataset")

    timed_backend = _TimedEmbeddingBackend(registry.backend)
    semantic = SemanticRouter(
        AgentDiscovery(registry.versions, registry.index, registry.validator, timed_backend),
        threshold=threshold, top_k=top_k,
    )
    router = SemanticLLMRouter(semantic, reranker) if strategy == "semantic_llm" else semantic
    config = BenchmarkConfig(
        catalog_snapshot_id=dataset.catalog_snapshot_id,
        catalog_agents=dataset.catalog_agents,
        dataset_version=dataset.dataset_version,
        split=split,
        embedding_model_key=timed_backend.model_key,
        top_k=top_k,
        threshold_value=threshold.value,
        threshold_source=threshold.source,
        strategy=strategy,
        rerank_model_key=rerank_model_key,
        environment=BenchmarkEnvironment(
            label=environment_label,
            python_version=sys.version.split()[0],
            platform=platform.platform(),
        ),
    )
    config_id = hashlib.sha256(json.dumps(
        config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()

    results = []
    for case in cases:
        if _catalog_refs(registry) != expected_refs:
            raise ValueError("catalog snapshot changed during benchmark")
        timed_backend.started_ns = None
        decision = router.route(case.task_text)
        finished_ns = time.monotonic_ns()
        if _catalog_refs(registry) != expected_refs:
            raise ValueError("catalog snapshot changed during benchmark")

        candidates = tuple(TraceCandidate(
            agent=TraceAgent(agent_id=item.agent_id, version=item.version),
            raw_similarity=item.raw_similarity,
        ) for item in decision.candidates)
        selected = (TraceAgent(
            agent_id=decision.selected_agent.agent_id,
            version=decision.selected_agent.version,
        ) if decision.selected_agent is not None else None)
        rerank = None
        if decision.rerank_result is not None:
            by_id = {item.agent.agent_id: item.agent for item in candidates}
            rerank = TraceRerank(
                input_candidates=tuple(item.agent for item in candidates),
                ordered_candidates=tuple(
                    by_id[agent_id] for agent_id in decision.rerank_result.ordered_candidate_ids
                ),
                model_key=rerank_model_key,
                reason_code=decision.rerank_result.reason_code,
            )
        started_ns = timed_backend.started_ns
        results.append(BenchmarkCaseResult(
            config_id=config_id,
            case_id=case.case_id,
            status="infra_error" if decision.status == "failed" else decision.status,
            semantic_candidates=candidates,
            selected_agent=selected,
            rejection_reason=decision.reason_code,
            error_code=decision.error_code,
            rerank=rerank,
            routing_latency_ms=(finished_ns - started_ns) / 1_000_000 if started_ns is not None else None,
            timing_scope="query_to_decision" if started_ns is not None else "query_not_started",
        ))
    return BenchmarkRun(config_id=config_id, config=config, cases=tuple(results))
