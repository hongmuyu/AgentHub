"""T41 routes labeled cases with fake services and never starts a workflow."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.discovery import discovery_text
from server.services.agenthub.embeddings import FakeEmbeddingBackend
from server.services.agenthub.evaluation_dataset import RoutingDataset
from server.services.agenthub.evaluation_runner import run_routing_benchmark
from server.services.agenthub.metadata import AgentMetadataInput
from server.services.agenthub.registry import AgentRegistry
from server.services.agenthub.reranker import RerankAdapter
from server.services.agenthub.router import CalibratedThreshold
from server.services.agenthub.runtime_resolver import RuntimeRefResolver
from server.services.agenthub.thin_workflow import ThinWorkflowValidator

ROOT = Path(__file__).resolve().parents[1] / "yaml_instance"
SELECT_TASK = "Compare supplied technical options"
REJECT_TASK = "Book a meeting room in the calendar"
ERROR_TASK = "Unconfigured fake embedding query"


@pytest.fixture
def catalog_and_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "fixture-model")
    monkeypatch.setenv("BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("API_KEY", "fixture-only")
    contents = [AgentMetadataInput.model_validate_json(
        (ROOT / f"agenthub_{stem}_metadata.json").read_text(encoding="utf-8")
    ) for stem in ("research", "code")]
    vectors = {
        discovery_text(contents[0]): (1.0, 0.0),
        discovery_text(contents[1]): (0.8, 0.6),
        SELECT_TASK: (1.0, 0.0),
        REJECT_TASK: (-1.0, 0.0),
    }
    registry = AgentRegistry(
        AgentHubDatabase(tmp_path / "catalog.db"),
        ThinWorkflowValidator(RuntimeRefResolver(ROOT)),
        FakeEmbeddingBackend(vectors, model_key="fake-eval-v1", dimensions=2),
    )
    first, second = (registry.register(content) for content in contents)
    dataset = RoutingDataset.model_validate({
        "dataset_version": "fixture-v1",
        "catalog_snapshot_id": "fixture-catalog-v1",
        "catalog_agents": [
            {"agent_id": agent.snapshot.id, "version": agent.snapshot.version,
             "status": agent.status}
            for agent in (first, second)
        ],
        "cases": [
            {"case_id": "selected-case", "task_text": SELECT_TASK,
             "expected_agent_ids": [first.snapshot.id], "should_reject": False,
             "category": "clear", "annotation_reason": "Research can compare options.",
             "dataset_version": "fixture-v1", "split": "calibration"},
            {"case_id": "reject-case", "task_text": REJECT_TASK,
             "expected_agent_ids": [], "should_reject": True,
             "category": "no_match", "annotation_reason": "No calendar action.",
             "dataset_version": "fixture-v1", "split": "test"},
            {"case_id": "error-case", "task_text": ERROR_TASK,
             "expected_agent_ids": [first.snapshot.id], "should_reject": False,
             "category": "clear", "annotation_reason": "Fake backend failure fixture.",
             "dataset_version": "fixture-v1", "split": "test"},
        ],
    })
    return registry, dataset, first, second


def _run(registry, dataset, *, strategy="semantic", split="calibration", **kwargs):
    return run_routing_benchmark(
        dataset, registry, strategy=strategy, split=split, top_k=2,
        threshold=CalibratedThreshold(0.5, "fixture-calibration-v1"),
        environment_label="local-fake", **kwargs,
    )


def test_semantic_output_is_repeatable_and_records_provenance_without_execution(
    catalog_and_dataset,
):
    registry, dataset, first, second = catalog_and_dataset
    with patch("workflow.graph.GraphExecutor.run") as workflow_run, patch(
        "server.services.workflow_run_service.WorkflowRunService.start_workflow"
    ) as service_run:
        first_run = _run(registry, dataset)
        second_run = _run(registry, dataset)

    assert first_run.config == second_run.config
    assert first_run.config_id == second_run.config_id
    assert first_run.config.catalog_snapshot_id == "fixture-catalog-v1"
    assert first_run.config.dataset_version == "fixture-v1"
    assert first_run.config.split == "calibration"
    assert first_run.config.embedding_model_key == "fake-eval-v1"
    assert first_run.config.top_k == 2
    assert first_run.config.threshold_value == 0.5
    assert first_run.config.threshold_source == "fixture-calibration-v1"
    assert first_run.config.strategy == "semantic"
    assert first_run.config.environment.label == "local-fake"
    assert first_run.config.environment.python_version
    assert first_run.config.environment.platform
    assert len(first_run.config.catalog_agents) == 2

    row = first_run.cases[0]
    assert len(first_run.cases) == 1
    assert row.case_id == "selected-case"
    assert row.config_id == first_run.config_id
    assert row.status == "selected"
    assert (row.selected_agent.agent_id, row.selected_agent.version) == (
        first.snapshot.id, first.snapshot.version,
    )
    assert [(candidate.agent.agent_id, candidate.agent.version) for candidate in
            row.semantic_candidates] == [
        (first.snapshot.id, 1), (second.snapshot.id, 1),
    ]
    assert [candidate.raw_similarity for candidate in row.semantic_candidates] == pytest.approx(
        [1.0, 0.8]
    )
    assert row.rerank is None
    assert row.error_code is None
    assert row.rejection_reason is None
    assert [(case.status, case.selected_agent, case.semantic_candidates) for case in
            first_run.cases] == [(case.status, case.selected_agent, case.semantic_candidates)
                                for case in second_run.cases]
    assert 0 <= row.routing_latency_ms < 1000
    assert first_run.model_validate_json(first_run.model_dump_json()) == first_run
    workflow_run.assert_not_called()
    service_run.assert_not_called()


def test_reranker_can_select_another_semantic_candidate_without_workflow(catalog_and_dataset):
    registry, dataset, first, second = catalog_and_dataset

    class ReverseTransport:
        def complete(self, request):
            return json.dumps({
                "candidate_ids": [request["candidates"][1]["id"],
                                  request["candidates"][0]["id"]],
                "reason_code": "CROSS_CAPABILITY",
            })

    with patch("workflow.graph.GraphExecutor.run") as workflow_run:
        report = _run(
            registry, dataset, strategy="semantic_llm",
            reranker=RerankAdapter(ReverseTransport()), rerank_model_key="fake-rerank-v1",
        )
        repeated = _run(
            registry, dataset, strategy="semantic_llm",
            reranker=RerankAdapter(ReverseTransport()), rerank_model_key="fake-rerank-v1",
        )

    row = report.cases[0]
    assert report.config.strategy == "semantic_llm"
    assert report.config.rerank_model_key == "fake-rerank-v1"
    assert [item.agent.agent_id for item in row.semantic_candidates] == [
        first.snapshot.id, second.snapshot.id,
    ]
    assert row.selected_agent.agent_id == second.snapshot.id
    assert [item.agent_id for item in row.rerank.ordered_candidates] == [
        second.snapshot.id, first.snapshot.id,
    ]
    assert row.rerank.reason_code == "CROSS_CAPABILITY"
    assert 0 <= row.routing_latency_ms < 1000
    assert report.config_id == repeated.config_id
    assert row.semantic_candidates == repeated.cases[0].semantic_candidates
    assert row.selected_agent == repeated.cases[0].selected_agent
    assert row.rerank == repeated.cases[0].rerank
    workflow_run.assert_not_called()


def test_rejection_and_embedding_error_remain_distinct_and_timed(catalog_and_dataset):
    registry, dataset, _, _ = catalog_and_dataset
    report = _run(registry, dataset, split="test")

    assert [row.case_id for row in report.cases] == ["reject-case", "error-case"]
    rejected, failed = report.cases
    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "NO_SUITABLE_AGENT"
    assert rejected.error_code is None
    assert rejected.selected_agent is None
    assert len(rejected.semantic_candidates) == 2
    assert failed.status == "infra_error"
    assert failed.error_code == "ROUTING_INFRASTRUCTURE_ERROR"
    assert failed.rejection_reason is None
    assert failed.selected_agent is None
    assert failed.semantic_candidates == ()
    assert all(row.config_id == report.config_id for row in report.cases)
    assert all(row.routing_latency_ms is not None and 0 <= row.routing_latency_ms < 1000
               for row in report.cases)
    assert "not configured" not in report.model_dump_json()


def test_rerank_timeout_keeps_semantic_candidates_as_infrastructure_error(catalog_and_dataset):
    registry, dataset, first, second = catalog_and_dataset

    class TimeoutTransport:
        def complete(self, request):
            raise TimeoutError("private detail")

    report = _run(
        registry, dataset, strategy="semantic_llm",
        reranker=RerankAdapter(TimeoutTransport()), rerank_model_key="fake-rerank-v1",
    )
    row = report.cases[0]
    assert row.status == "infra_error"
    assert row.error_code == "RERANK_TIMEOUT"
    assert row.selected_agent is None
    assert row.rejection_reason is None
    assert row.rerank is None
    assert [item.agent.agent_id for item in row.semantic_candidates] == [
        first.snapshot.id, second.snapshot.id,
    ]
    assert 0 <= row.routing_latency_ms < 1000
    assert "private detail" not in report.model_dump_json()


def test_changed_catalog_snapshot_is_rejected_before_route(catalog_and_dataset):
    registry, dataset, first, _ = catalog_and_dataset
    registry.disable(first.snapshot.id)
    with patch("workflow.graph.GraphExecutor.run") as workflow_run, pytest.raises(
        ValueError, match="catalog snapshot"
    ):
        _run(registry, dataset)
    workflow_run.assert_not_called()


def test_pre_embedding_infrastructure_error_has_no_invented_latency(catalog_and_dataset):
    registry, dataset, _, _ = catalog_and_dataset
    with patch.object(registry.index, "require_backend", side_effect=RuntimeError("private detail")):
        report = _run(registry, dataset)

    row = report.cases[0]
    assert row.status == "infra_error"
    assert row.error_code == "ROUTING_INFRASTRUCTURE_ERROR"
    assert row.routing_latency_ms is None
    assert row.timing_scope == "query_not_started"
    assert "private detail" not in report.model_dump_json()


def test_rerank_config_and_unresolved_attachment_are_not_silently_used(
    catalog_and_dataset,
):
    registry, dataset, _, _ = catalog_and_dataset
    with pytest.raises(ValueError, match="reranker"):
        _run(registry, dataset, strategy="semantic_llm")

    payload = dataset.model_dump(mode="json")
    payload["attachment_fixture_refs"] = ["fixture://sample"]
    payload["cases"][0]["attachment_fixture_ref"] = "fixture://sample"
    with pytest.raises(ValueError, match="attachment"):
        _run(registry, RoutingDataset.model_validate(payload))
