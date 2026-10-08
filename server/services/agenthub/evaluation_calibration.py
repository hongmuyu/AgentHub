"""Choose a shared routing gate from calibration runs, then lock it for test."""

from dataclasses import dataclass

from .evaluation_dataset import RoutingDataset
from .evaluation_metrics import RoutingMetricsReport, calculate_routing_metrics
from .evaluation_runner import BenchmarkRun, run_routing_benchmark
from .registry import AgentRegistry
from .reranker import RerankAdapter
from .router import CalibratedThreshold


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
    """Rank common gates by balanced recall/rejection; never inspect test rows.

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
    # Equal weight to route recall and no-match rejection; smaller K breaks ties.
    best = max(range(len(runs)), key=lambda index: (
        reports[index].top_k_recall.value + reports[index].reject_accuracy.value,
        reports[index].top1_accuracy.value,
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
