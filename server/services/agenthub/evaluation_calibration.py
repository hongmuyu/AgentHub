"""Choose a shared routing gate from calibration runs, then lock it for test."""

import hashlib
import json
from dataclasses import dataclass

from .evaluation_dataset import RoutingDataset
from .evaluation_metrics import RoutingMetricsReport, calculate_routing_metrics
from .evaluation_runner import BenchmarkCaseResult, BenchmarkRun, run_routing_benchmark
from .registry import AgentRegistry
from .reranker import RerankAdapter
from .router import CalibratedThreshold


def derive_calibration_runs(
    baseline: BenchmarkRun, *, top_ks: tuple[int, ...], thresholds: tuple[float, ...]
) -> tuple[BenchmarkRun, ...]:
    """Sweep gates over measured calibration scores without new provider calls.

    Derived rows have no measured per-gate latency; baseline retains actual
    query timings. Only the semantic candidate order and raw cosine are used.
    """
    if (baseline.config.strategy != "semantic" or baseline.config.split != "calibration"
            or baseline.config.threshold_value != -1.0
            or any(row.status == "infra_error" for row in baseline.cases)):
        raise ValueError("gate sweep needs an error-free, ungated calibration run")
    if (not top_ks or not thresholds or
            any(type(k) is not int or not 0 < k <= baseline.config.top_k for k in top_ks)
            or len(set(top_ks)) != len(top_ks)
            or len(set(thresholds)) != len(thresholds)):
        raise ValueError("invalid calibration gate grid")

    runs = []
    for k in top_ks:
        for threshold in thresholds:
            calibrated = CalibratedThreshold(threshold, "calibration-gate-sweep")
            config = baseline.config.model_copy(update={
                "top_k": k,
                "threshold_value": calibrated.value,
                "threshold_source": f"derived-from:{baseline.config_id}",
            })
            config_id = hashlib.sha256(json.dumps(
                config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            rows = []
            for row in baseline.cases:
                candidates = row.semantic_candidates[:k]
                selected = (candidates[0].agent if candidates and
                            candidates[0].raw_similarity >= calibrated.value else None)
                rows.append(BenchmarkCaseResult(
                    config_id=config_id,
                    case_id=row.case_id,
                    status="selected" if selected is not None else "rejected",
                    semantic_candidates=candidates,
                    selected_agent=selected,
                    rejection_reason=None if selected is not None else "NO_SUITABLE_AGENT",
                    timing_scope="query_not_started",
                ))
            runs.append(BenchmarkRun(config_id=config_id, config=config, cases=tuple(rows)))
    return tuple(runs)


@dataclass(frozen=True)
class FrozenRoutingConfig:
    top_k: int
    threshold: CalibratedThreshold
    calibration_config_id: str
    dataset_version: str
    catalog_snapshot_id: str
    embedding_model_key: str


def select_calibration_config(
    dataset: RoutingDataset, runs: tuple[BenchmarkRun, ...]
) -> tuple[FrozenRoutingConfig, tuple[RoutingMetricsReport, ...]]:
    """Rank common gates by balanced routed accuracy/rejection; never inspect test rows.

    All candidate runs must use the same calibration catalog, dataset, model,
    and strategy. Infrastructure errors or empty label groups prevent freezing.
    """
    if not runs or any(run.config.split != "calibration" or
                       run.config.strategy != "semantic" for run in runs):
        raise ValueError("calibration requires semantic calibration runs")
    first = runs[0].config
    if any((run.config.catalog_snapshot_id, run.config.dataset_version,
            run.config.embedding_model_key, run.config.environment,
            run.config.catalog_agents) !=
           (first.catalog_snapshot_id, first.dataset_version,
            first.embedding_model_key, first.environment,
            first.catalog_agents) for run in runs):
        raise ValueError("calibration candidate provenance differs")
    if len({(run.config.top_k, run.config.threshold_value) for run in runs}) != len(runs):
        raise ValueError("calibration candidate gate repeats")

    reports = tuple(calculate_routing_metrics(dataset, run) for run in runs)
    if any(report.infra_error_case_ids or report.top1_accuracy.denominator == 0 or
           report.reject_accuracy.denominator == 0 for report in reports):
        raise ValueError("calibration requires valid route and no-match samples")
    # Top-K recall is gate-independent; score the threshold with routed accuracy.
    best = max(range(len(runs)), key=lambda index: (
        reports[index].top1_accuracy.value + reports[index].reject_accuracy.value,
        reports[index].top_k_recall.value,
        -runs[index].config.top_k,
        runs[index].config.threshold_value,
    ))
    chosen = runs[best]
    return FrozenRoutingConfig(
        top_k=chosen.config.top_k,
        threshold=CalibratedThreshold(
            chosen.config.threshold_value,
            f"calibration:{chosen.config_id}",
        ),
        calibration_config_id=chosen.config_id,
        dataset_version=first.dataset_version,
        catalog_snapshot_id=first.catalog_snapshot_id,
        embedding_model_key=first.embedding_model_key,
    ), reports


def run_frozen_test(
    dataset: RoutingDataset, registry: AgentRegistry, frozen: FrozenRoutingConfig,
    *, environment_label: str, reranker: RerankAdapter, rerank_model_key: str,
) -> tuple[tuple[BenchmarkRun, RoutingMetricsReport], ...]:
    """Evaluate both strategies on held-out cases with the same frozen gate."""
    if (dataset.dataset_version != frozen.dataset_version or
            dataset.catalog_snapshot_id != frozen.catalog_snapshot_id or
            registry.backend.model_key != frozen.embedding_model_key):
        raise ValueError("frozen calibration provenance differs from test inputs")
    results = []
    for strategy in ("semantic", "semantic_llm"):
        run = run_routing_benchmark(
            dataset, registry, strategy=strategy, split="test",
            top_k=frozen.top_k, threshold=frozen.threshold,
            environment_label=environment_label,
            reranker=reranker if strategy == "semantic_llm" else None,
            rerank_model_key=rerank_model_key if strategy == "semantic_llm" else None,
        )
        results.append((run, calculate_routing_metrics(dataset, run)))
    return tuple(results)
