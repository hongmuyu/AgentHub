"""Auditable routing metrics from labeled cases and measured runner decisions."""

import math
from statistics import fmean

from pydantic import BaseModel, ConfigDict, Field

from .evaluation_dataset import RoutingDataset
from .evaluation_runner import BenchmarkRun


class MetricFraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class RoutingLatencySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_count: int = Field(ge=0)
    mean_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    p50_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    p95_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    included_infra_error_case_ids: tuple[str, ...]
    unmeasured_case_ids: tuple[str, ...]


class RoutingMetricsReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_id: str
    strategy: str
    dataset_version: str
    split: str
    total_case_count: int = Field(ge=0)
    applicable_case_count: int = Field(ge=0)
    no_match_case_count: int = Field(ge=0)
    valid_route_count: int = Field(ge=0)
    infra_error_case_ids: tuple[str, ...]
    top1_accuracy: MetricFraction
    top_k_recall: MetricFraction
    reject_accuracy: MetricFraction
    false_accept_rate: MetricFraction
    top1_incorrect_case_ids: tuple[str, ...]
    top_k_miss_case_ids: tuple[str, ...]
    false_accept_case_ids: tuple[str, ...]
    latency: RoutingLatencySummary


def _fraction(numerator: int, denominator: int) -> MetricFraction:
    return MetricFraction(
        numerator=numerator, denominator=denominator,
        value=numerator / denominator if denominator else None,
    )


def _latency(rows) -> RoutingLatencySummary:
    measured = sorted(row.routing_latency_ms for row in rows if row.routing_latency_ms is not None)
    count = len(measured)
    return RoutingLatencySummary(
        sample_count=count,
        mean_ms=fmean(measured) if measured else None,
        p50_ms=measured[math.ceil(0.50 * count) - 1] if measured else None,
        p95_ms=measured[math.ceil(0.95 * count) - 1] if measured else None,
        included_infra_error_case_ids=tuple(
            row.case_id for row in rows
            if row.status == "infra_error" and row.routing_latency_ms is not None
        ),
        unmeasured_case_ids=tuple(
            row.case_id for row in rows if row.routing_latency_ms is None
        ),
    )


def calculate_routing_metrics(dataset: RoutingDataset, run: BenchmarkRun) -> RoutingMetricsReport:
    """Exclude infrastructure errors from quality rates; retain measured latency."""
    config = run.config
    if (config.catalog_snapshot_id != dataset.catalog_snapshot_id
            or {(ref.agent_id, ref.version, ref.status) for ref in config.catalog_agents}
            != {(ref.agent_id, ref.version, ref.status) for ref in dataset.catalog_agents}):
        raise ValueError("catalog snapshot differs from dataset")
    if config.dataset_version != dataset.dataset_version:
        raise ValueError("dataset version differs from benchmark config")

    cases = tuple(case for case in dataset.cases if case.split == config.split)
    case_ids = {case.case_id for case in cases}
    rows = run.cases
    if len(rows) != len(cases) or {row.case_id for row in rows} != case_ids:
        raise ValueError("benchmark case rows do not match dataset split")
    if any(row.config_id != run.config_id for row in rows):
        raise ValueError("benchmark case config_id differs from run config")

    catalog_agents = {(ref.agent_id, ref.version) for ref in config.catalog_agents}
    for row in rows:
        recalled = tuple(candidate.agent for candidate in row.semantic_candidates)
        if (len(recalled) > config.top_k
                or any((agent.agent_id, agent.version) not in catalog_agents for agent in recalled)
                or (row.selected_agent is not None and row.selected_agent not in recalled)):
            raise ValueError("benchmark candidate is outside the configured catalog or Top-K")

    by_id = {row.case_id: row for row in rows}
    applicable = tuple(case for case in cases if not case.should_reject)
    no_match = tuple(case for case in cases if case.should_reject)
    valid_applicable = tuple(case for case in applicable if by_id[case.case_id].status != "infra_error")
    valid_no_match = tuple(case for case in no_match if by_id[case.case_id].status != "infra_error")

    top1_incorrect = tuple(
        case.case_id for case in valid_applicable
        if (by_id[case.case_id].status != "selected"
            or by_id[case.case_id].selected_agent.agent_id not in case.expected_agent_ids)
    )
    top_k_misses = tuple(
        case.case_id for case in valid_applicable
        if not any(candidate.agent.agent_id in case.expected_agent_ids
                   for candidate in by_id[case.case_id].semantic_candidates)
    )
    false_accepts = tuple(
        case.case_id for case in valid_no_match if by_id[case.case_id].status == "selected"
    )
    correct_rejects = sum(by_id[case.case_id].status == "rejected" for case in valid_no_match)
    return RoutingMetricsReport(
        config_id=run.config_id,
        strategy=config.strategy,
        dataset_version=config.dataset_version,
        split=config.split,
        total_case_count=len(cases),
        applicable_case_count=len(applicable),
        no_match_case_count=len(no_match),
        valid_route_count=sum(row.status != "infra_error" for row in rows),
        infra_error_case_ids=tuple(case.case_id for case in cases
                                   if by_id[case.case_id].status == "infra_error"),
        top1_accuracy=_fraction(len(valid_applicable) - len(top1_incorrect), len(valid_applicable)),
        top_k_recall=_fraction(len(valid_applicable) - len(top_k_misses), len(valid_applicable)),
        reject_accuracy=_fraction(correct_rejects, len(valid_no_match)),
        false_accept_rate=_fraction(len(false_accepts), len(valid_no_match)),
        top1_incorrect_case_ids=top1_incorrect,
        top_k_miss_case_ids=top_k_misses,
        false_accept_case_ids=false_accepts,
        latency=_latency(rows),
    )
