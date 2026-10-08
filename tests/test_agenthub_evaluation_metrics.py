"""T42 metric denominators and exclusions come from hand-counted cases."""

from uuid import UUID

import pytest

from server.services.agenthub.evaluation_dataset import RoutingDataset
from server.services.agenthub.evaluation_metrics import calculate_routing_metrics
from server.services.agenthub.evaluation_runner import (
    BenchmarkCaseResult,
    BenchmarkConfig,
    BenchmarkEnvironment,
    BenchmarkRun,
)
from server.services.agenthub.routing_traces import (
    TraceAgent,
    TraceCandidate,
    TraceRerank,
)

A, B, C = (UUID(int=number) for number in (1, 2, 3))
AGENTS = (A, B, C)


def _case(case_id, category, expected):
    return {
        "case_id": case_id,
        "task_text": f"Synthetic task for {case_id}",
        "expected_agent_ids": expected,
        "should_reject": category == "no_match",
        "category": category,
        "annotation_reason": "Hand-counted metric fixture.",
        "dataset_version": "metrics-v1",
        "split": "test",
    }


def _dataset(cases):
    return RoutingDataset.model_validate({
        "dataset_version": "metrics-v1",
        "catalog_snapshot_id": "metrics-catalog-v1",
        "catalog_agents": [
            {"agent_id": agent_id, "version": 1, "status": "active"}
            for agent_id in AGENTS
        ],
        "cases": cases,
    })


def _candidate(agent_id):
    return TraceCandidate(
        agent=TraceAgent(agent_id=agent_id, version=1), raw_similarity=0.8,
    )


def _row(case_id, status, candidates=(), selected=None, latency=None):
    return BenchmarkCaseResult(
        config_id="fixture-config",
        case_id=case_id,
        status=status,
        semantic_candidates=tuple(_candidate(agent_id) for agent_id in candidates),
        selected_agent=TraceAgent(agent_id=selected, version=1) if selected else None,
        rejection_reason="NO_SUITABLE_AGENT" if status == "rejected" else None,
        error_code="ROUTING_INFRASTRUCTURE_ERROR" if status == "infra_error" else None,
        routing_latency_ms=latency,
        timing_scope="query_to_decision" if latency is not None else "query_not_started",
    )


def _run(dataset, rows, *, strategy="semantic", rerank_model_key=None):
    return BenchmarkRun(
        config_id="fixture-config",
        config=BenchmarkConfig(
            catalog_snapshot_id=dataset.catalog_snapshot_id,
            catalog_agents=dataset.catalog_agents,
            dataset_version=dataset.dataset_version,
            split="test",
            embedding_model_key="fake-metrics-v1",
            top_k=2,
            threshold_value=0.4,
            threshold_source="fixture-calibration-v1",
            strategy=strategy,
            rerank_model_key=rerank_model_key,
            environment=BenchmarkEnvironment(
                label="local-fake", python_version="3.12", platform="fixture-linux",
            ),
        ),
        cases=tuple(rows),
    )


def test_hand_counted_quality_metrics_errors_and_latency_denominators():
    # Five route-required cases: four valid, two correct Top-1, three semantic Top-K hits.
    # Three no-match cases: two valid, one correct reject and one false accept.
    # Two infrastructure errors never enter quality denominators; one has measured latency.
    dataset = _dataset([
        _case("clear-correct", "clear", [A]),
        _case("clear-wrong", "clear", [A]),
        _case("ambiguous-correct", "ambiguous", [A, B]),
        _case("ambiguous-rejected", "ambiguous", [A, B]),
        _case("clear-error", "clear", [A]),
        _case("reject-correct", "no_match", []),
        _case("reject-false-accept", "no_match", []),
        _case("reject-error", "no_match", []),
    ])
    run = _run(dataset, [
        _row("clear-correct", "selected", (A, B), A, 10),
        _row("clear-wrong", "selected", (B, A), B, 20),
        _row("ambiguous-correct", "selected", (B, C), B, 30),
        _row("ambiguous-rejected", "rejected", (C,), latency=40),
        _row("clear-error", "infra_error", latency=50),
        _row("reject-correct", "rejected", (C,), latency=60),
        _row("reject-false-accept", "selected", (A,), A, 70),
        _row("reject-error", "infra_error"),
    ])

    report = calculate_routing_metrics(dataset, run)

    assert report.config_id == "fixture-config"
    assert report.strategy == "semantic"
    assert report.split == "test"
    assert report.total_case_count == 8
    assert report.applicable_case_count == 5
    assert report.no_match_case_count == 3
    assert report.valid_route_count == 6
    assert report.infra_error_case_ids == ("clear-error", "reject-error")
    assert (report.top1_accuracy.numerator, report.top1_accuracy.denominator,
            report.top1_accuracy.value) == (2, 4, 0.5)
    assert (report.top_k_recall.numerator, report.top_k_recall.denominator,
            report.top_k_recall.value) == (3, 4, 0.75)
    assert (report.reject_accuracy.numerator, report.reject_accuracy.denominator,
            report.reject_accuracy.value) == (1, 2, 0.5)
    assert (report.false_accept_rate.numerator, report.false_accept_rate.denominator,
            report.false_accept_rate.value) == (1, 2, 0.5)
    assert report.top1_incorrect_case_ids == ("clear-wrong", "ambiguous-rejected")
    assert report.top_k_miss_case_ids == ("ambiguous-rejected",)
    assert report.false_accept_case_ids == ("reject-false-accept",)
    # Seven measured values are 10, 20, 30, 40, 50, 60, 70 ms.
    assert report.latency.sample_count == 7
    assert report.latency.mean_ms == 40
    assert report.latency.p50_ms == 40
    assert report.latency.p95_ms == 70
    assert report.latency.included_infra_error_case_ids == ("clear-error",)
    assert report.latency.unmeasured_case_ids == ("reject-error",)
    assert report.model_validate_json(report.model_dump_json()) == report


def test_semantic_top_k_recall_does_not_use_rerank_order():
    dataset = _dataset([_case("reranked-wrong", "clear", [A])])
    semantic = (_candidate(A), _candidate(B))
    row = _row("reranked-wrong", "selected", (A, B), B, 5).model_copy(update={
        "rerank": TraceRerank(
            input_candidates=tuple(item.agent for item in semantic),
            ordered_candidates=(semantic[1].agent, semantic[0].agent),
            model_key="fake-rerank-v1", reason_code="CAPABILITY_MATCH",
        ),
    })

    report = calculate_routing_metrics(
        dataset, _run(dataset, [row], strategy="semantic_llm", rerank_model_key="fake-rerank-v1")
    )

    assert report.strategy == "semantic_llm"
    assert (report.top1_accuracy.numerator, report.top1_accuracy.denominator) == (0, 1)
    assert (report.top_k_recall.numerator, report.top_k_recall.denominator) == (1, 1)
    assert report.latency.sample_count == 1
    assert report.latency.mean_ms == report.latency.p50_ms == report.latency.p95_ms == 5


def test_empty_split_and_all_error_samples_are_unmeasured():
    calibration = _case("calibration-only", "clear", [A])
    calibration["split"] = "calibration"
    dataset = _dataset([calibration])
    report = calculate_routing_metrics(dataset, _run(dataset, []))

    assert report.total_case_count == 0
    for metric in (report.top1_accuracy, report.top_k_recall,
                   report.reject_accuracy, report.false_accept_rate):
        assert (metric.numerator, metric.denominator, metric.value) == (0, 0, None)
    assert report.latency.sample_count == 0
    assert report.latency.mean_ms is None
    assert report.latency.p50_ms is None
    assert report.latency.p95_ms is None

    failed_dataset = _dataset([_case("only-error", "clear", [A])])
    failed = calculate_routing_metrics(
        failed_dataset, _run(failed_dataset, [_row("only-error", "infra_error")])
    )
    assert failed.applicable_case_count == 1
    assert failed.valid_route_count == 0
    assert failed.infra_error_case_ids == ("only-error",)
    assert failed.top1_accuracy.value is None
    assert failed.top_k_recall.value is None
    assert failed.latency.sample_count == 0
    assert failed.latency.unmeasured_case_ids == ("only-error",)


def test_mismatched_or_incomplete_runner_rows_are_rejected():
    dataset = _dataset([_case("expected", "clear", [A])])
    with pytest.raises(ValueError, match="case"):
        calculate_routing_metrics(dataset, _run(dataset, []))

    wrong_config = _row("expected", "selected", (A,), A, 1).model_copy(
        update={"config_id": "other-config"}
    )
    with pytest.raises(ValueError, match="config"):
        calculate_routing_metrics(dataset, _run(dataset, [wrong_config]))

    changed_snapshot = _run(dataset, [_row("expected", "selected", (A,), A, 1)])
    changed_snapshot = changed_snapshot.model_copy(update={
        "config": changed_snapshot.config.model_copy(update={"catalog_snapshot_id": "other"}),
    })
    with pytest.raises(ValueError, match="snapshot"):
        calculate_routing_metrics(dataset, changed_snapshot)
