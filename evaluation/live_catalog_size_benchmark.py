"""Explicit live-provider latency measurement on synthetic 10/50/100 catalogs."""

import argparse
import hashlib
import json
import os
import tempfile
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from evaluation.catalog_size_benchmark import _catalog, _hardware, _measure
from evaluation.live_routing_benchmark import _require_provider_configuration
from server.services.agenthub.openai_embeddings import OpenAICompatibleEmbeddingBackend
from server.services.agenthub.openai_rerank import OpenAICompatibleRerankTransport
from utils.env_loader import load_dotenv_file


def run_live_catalog_sizes(output: Path) -> dict:
    load_dotenv_file()
    _require_provider_configuration()
    backend = OpenAICompatibleEmbeddingBackend.from_environment()
    transport = OpenAICompatibleRerankTransport.from_environment()
    report = {
        "label": "live_provider_synthetic_catalog_latency_only",
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "hardware": _hardware(),
        "config": {
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "shared_measurement_source_sha256": hashlib.sha256(
                Path(_measure.__code__.co_filename).read_bytes()
            ).hexdigest(),
            "embedding_provider": "DashScope",
            "embedding_model_key": backend.model_key,
            "embedding_dimensions": backend.dimensions,
            "rerank_provider": "DeepSeek",
            "rerank_model_key": transport.model_key,
            "catalog_sizes": [10, 50, 100],
            "catalog_construction": "synthetic version-1 active metadata, real indexed embeddings, allowlisted thin workflow; no workflow execution",
            "top_k": 3,
            "threshold": -1.0,
            "threshold_source": "scale-scan-no-gate-v1",
            "warmup_requests_per_strategy": 1,
            "query_count": 12,
            "repeats_per_query": 1,
            "cache": "new temporary SQLite catalog per size; OS/provider caches uncontrolled; one local warmup per strategy",
            "timing_scope": "shared with deterministic catalog-size benchmark; rerank segment includes live DeepSeek request",
            "quality_scope": "synthetic catalog/tasks; no routing accuracy or quality claims",
        },
        "sizes": [],
    }
    validation_env = {
        "MODEL_NAME": "t43-validation-only",
        "BASE_URL": "https://example.invalid/v1",
        "API_KEY": "t43-validation-only",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    for size in (10, 50, 100):
        with patch.dict(os.environ, validation_env), tempfile.TemporaryDirectory(
            prefix="agenthub-t43-live-size-"
        ) as directory, open(os.devnull, "w", encoding="utf-8") as quiet, redirect_stdout(quiet):
            registry, build_ms = _catalog(Path(directory), size, backend=backend)
            strategies = _measure(
                registry, size, top_k=3, warmup=1, repeats=1,
                rerank_transport=transport,
            )
        report["sizes"].append({
            "catalog_size": size,
            "catalog_and_index_build_ms": build_ms,
            "strategies": strategies,
        })
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("live_catalog_size_measured", size)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_live_catalog_sizes(args.output)


if __name__ == "__main__":
    main()
