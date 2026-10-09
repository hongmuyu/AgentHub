"""Explicit live T43 calibration, freeze, and independent test evaluation.

Run only with locally configured DashScope and DeepSeek credentials:
python -m evaluation.live_routing_benchmark --output-dir evaluation
"""

import argparse
import hashlib
import json
import math
import os
import platform
import sys
import tempfile
import time
from collections import Counter
from contextlib import redirect_stdout
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from evaluation.catalog_size_benchmark import _percentiles
from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.demo_catalog import register_demo_catalog
from server.services.agenthub.evaluation_calibration import (
    derive_calibration_runs,
    run_frozen_test,
    select_calibration_config,
)
from server.services.agenthub.evaluation_metrics import calculate_routing_metrics
from server.services.agenthub.evaluation_runner import run_routing_benchmark
from server.services.agenthub.full_dataset import build_full_dataset
from server.services.agenthub.openai_embeddings import OpenAICompatibleEmbeddingBackend
from server.services.agenthub.openai_rerank import OpenAICompatibleRerankTransport
from server.services.agenthub.registry import AgentRegistry
from server.services.agenthub.reranker import RerankAdapter
from server.services.agenthub.router import CalibratedThreshold
from server.services.agenthub.runtime_resolver import RuntimeRefResolver
from server.services.agenthub.thin_workflow import ThinWorkflowValidator
from utils.env_loader import load_dotenv_file

TOP_K_GRID = (1, 3, 5)
ENVIRONMENT_LABEL = "live-dashscope-deepseek"


class _TimedEmbedding:
    def __init__(self, backend):
        self.backend = backend
        self.model_key = backend.model_key
        self.dimensions = backend.dimensions
        self.samples_ms = []

    def embed(self, texts):
        started = time.perf_counter_ns()
        try:
            return self.backend.embed(texts)
        finally:
            self.samples_ms.append((time.perf_counter_ns() - started) / 1_000_000)


class _TimedRerankTransport:
    def __init__(self, transport):
        self.transport = transport
        self.model_key = transport.model_key
        self.samples_ms = []

    def complete(self, request):
        started = time.perf_counter_ns()
        try:
            return self.transport.complete(request)
        finally:
            self.samples_ms.append((time.perf_counter_ns() - started) / 1_000_000)


def _require_provider_configuration() -> None:
    pairs = (
        ("AGENTHUB_EMBEDDING_BASE_URL", "dashscope.aliyuncs.com"),
        ("AGENTHUB_RERANK_BASE_URL", "api.deepseek.com"),
    )
    for key, expected_host in pairs:
        try:
            url = urlsplit(os.environ.get(key, ""))
            valid = (url.scheme == "https" and url.hostname == expected_host
                     and not (url.username or url.password or url.query or url.fragment))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(f"{key} does not identify the expected HTTPS provider")
    if (os.environ.get("AGENTHUB_EMBEDDING_API_KEY_ENV") != "DASHSCOPE_API_KEY"
            or os.environ.get("AGENTHUB_RERANK_API_KEY_ENV") != "DEEPSEEK_API_KEY"
            or not os.environ.get("DASHSCOPE_API_KEY")
            or not os.environ.get("DEEPSEEK_API_KEY")):
        raise ValueError("live credential selector or credential is missing")


def _threshold_grid(baseline) -> tuple[float, ...]:
    if baseline.config.split != "calibration":
        raise ValueError("thresholds require calibration split")
    scores = sorted({
        max(-1.0, min(1.0, row.semantic_candidates[0].raw_similarity))
        for row in baseline.cases if row.semantic_candidates
    })
    if any(not math.isfinite(score) for score in scores):
        raise ValueError("non-finite calibration score")
    return tuple(sorted({-1.0, 1.0, *scores,
                         *((left + right) / 2 for left, right in pairwise(scores))}))


def _fraction(report):
    return {
        "top1": report.top1_accuracy.model_dump(mode="json"),
        "top_k": report.top_k_recall.model_dump(mode="json"),
        "reject": report.reject_accuracy.model_dump(mode="json"),
        "false_accept": report.false_accept_rate.model_dump(mode="json"),
        "infra_error_count": len(report.infra_error_case_ids),
    }


def _write_json(path: Path, content: dict) -> None:
    path.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_live_evaluation(output_dir: Path) -> tuple[Path, Path]:
    load_dotenv_file()
    _require_provider_configuration()
    backend = _TimedEmbedding(OpenAICompatibleEmbeddingBackend.from_environment())
    transport = _TimedRerankTransport(OpenAICompatibleRerankTransport.from_environment())
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).date().isoformat()
    calibration_path = output_dir / f"t43_live_calibration_{stamp}.json"
    test_path = output_dir / f"t43_live_test_{stamp}.json"
    provenance = {
        "label": "live_provider_measurement",
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "embedding_provider": "DashScope",
        "embedding_model_key": backend.model_key,
        "embedding_dimensions": backend.dimensions,
        "embedding_config_sha256": hashlib.sha256(json.dumps([
            os.environ["AGENTHUB_EMBEDDING_BASE_URL"],
            os.environ["AGENTHUB_EMBEDDING_MODEL"], backend.model_key,
            backend.dimensions,
        ]).encode()).hexdigest(),
        "rerank_provider": "DeepSeek",
        "rerank_model_key": transport.model_key,
        "rerank_config_sha256": hashlib.sha256(json.dumps([
            os.environ["AGENTHUB_RERANK_BASE_URL"], transport.model_key,
        ]).encode()).hexdigest(),
        "credential_sources": "ignored local .env; only selector names are used in process",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "logical_cpus": os.cpu_count(),
        "cache": "new temporary SQLite catalog/index; provider-side caches uncontrolled",
    }

    # Workflow fixtures need substitutions for validation; this benchmark never runs them.
    validation_env = {
        "MODEL_NAME": "t43-validation-only",
        "BASE_URL": "https://example.invalid/v1",
        "API_KEY": "t43-validation-only",
    }
    with patch.dict(os.environ, validation_env), tempfile.TemporaryDirectory(
        prefix="agenthub-t43-live-"
    ) as directory:
        registry = AgentRegistry(
            AgentHubDatabase(Path(directory) / "catalog.db"),
            ThinWorkflowValidator(RuntimeRefResolver()), backend,
        )
        with open(os.devnull, "w", encoding="utf-8") as quiet, redirect_stdout(quiet):
            started = time.perf_counter_ns()
            catalog = register_demo_catalog(registry)
            index_build_ms = (time.perf_counter_ns() - started) / 1_000_000
            dataset = build_full_dataset(catalog)
            index_embedding_samples = tuple(backend.samples_ms)
            backend.samples_ms.clear()
            counts = Counter((case.category, case.split) for case in dataset.cases)
            if counts != {
                ("clear", "calibration"): 18, ("clear", "test"): 18,
                ("ambiguous", "calibration"): 6, ("ambiguous", "test"): 6,
                ("no_match", "calibration"): 6, ("no_match", "test"): 6,
            }:
                raise ValueError("live dataset split/count differs from the reviewed 60 cases")

            baseline = run_routing_benchmark(
                dataset, registry, strategy="semantic", split="calibration",
                top_k=max(TOP_K_GRID),
                threshold=CalibratedThreshold(-1.0, "calibration-score-scan"),
                environment_label=ENVIRONMENT_LABEL,
            )
        baseline_metrics = calculate_routing_metrics(dataset, baseline)
        calibration_embedding_samples = tuple(backend.samples_ms)
        backend.samples_ms.clear()
        if baseline_metrics.infra_error_case_ids:
            _write_json(calibration_path, {
                "status": "blocked_infrastructure_error",
                "provenance": provenance,
                "catalog_index_build_ms": index_build_ms,
                "index_embedding_latency": _percentiles(index_embedding_samples),
                "calibration_query_embedding_latency": _percentiles(calibration_embedding_samples),
                "dataset_counts": {f"{category}:{split}": count for (category, split), count in counts.items()},
                "baseline_run": baseline.model_dump(mode="json"),
                "baseline_metrics": baseline_metrics.model_dump(mode="json"),
            })
            raise RuntimeError("calibration has infrastructure errors; no gate was frozen")

        thresholds = _threshold_grid(baseline)
        candidates = derive_calibration_runs(
            baseline, top_ks=TOP_K_GRID, thresholds=thresholds,
        )
        frozen, reports = select_calibration_config(dataset, candidates)
        selected_index = next(i for i, run in enumerate(candidates)
                              if run.config_id == frozen.calibration_config_id)
        _write_json(calibration_path, {
            "status": "frozen_before_test",
            "provenance": provenance,
            "catalog_index_build_ms": index_build_ms,
            "index_embedding_latency": _percentiles(index_embedding_samples),
            "calibration_query_embedding_latency": _percentiles(calibration_embedding_samples),
            "dataset_counts": {f"{category}:{split}": count for (category, split), count in counts.items()},
            "dataset_version": dataset.dataset_version,
            "catalog_snapshot_id": dataset.catalog_snapshot_id,
            "calibration_baseline_run": baseline.model_dump(mode="json"),
            "calibration_baseline_metrics": baseline_metrics.model_dump(mode="json"),
            "gate_selection_rule": "maximize Top-1 Accuracy + Reject Accuracy; then Top-K Recall; then smaller K; then higher threshold",
            "gate_grid": [{
                "config_id": run.config_id,
                "top_k": run.config.top_k,
                "threshold": run.config.threshold_value,
                "quality": _fraction(report),
                "latency": "not measured for derived gates; use baseline measurement",
            } for run, report in zip(candidates, reports, strict=True)],
            "frozen_config": {
                "top_k": frozen.top_k,
                "threshold": frozen.threshold.value,
                "threshold_source": frozen.threshold.source,
                "calibration_config_id": frozen.calibration_config_id,
                "selected_calibration_quality": _fraction(reports[selected_index]),
            },
        })
        print("calibration_frozen", len(baseline.cases), "cases", len(candidates), "derived_gates")

        with open(os.devnull, "w", encoding="utf-8") as quiet, redirect_stdout(quiet):
            results = run_frozen_test(
                dataset, registry, frozen,
                environment_label=ENVIRONMENT_LABEL,
                reranker=RerankAdapter(transport),
                rerank_model_key=transport.model_key,
            )
        _write_json(test_path, {
            "status": "measured_independent_test",
            "provenance": provenance,
            "calibration_artifact": calibration_path.name,
            "frozen_config_id": frozen.calibration_config_id,
            "test_query_embedding_latency": _percentiles(backend.samples_ms),
            "test_rerank_transport_latency": _percentiles(transport.samples_ms),
            "dataset_version": dataset.dataset_version,
            "catalog_snapshot_id": dataset.catalog_snapshot_id,
            "results": [{
                "run": run.model_dump(mode="json"),
                "metrics": metrics.model_dump(mode="json"),
            } for run, metrics in results],
        })
        print("independent_test_measured", len(results[0][0].cases), "cases_per_strategy")
    return calibration_path, test_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run_live_evaluation(args.output_dir)


if __name__ == "__main__":
    main()
