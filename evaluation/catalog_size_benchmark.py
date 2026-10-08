"""Offline exact-scan engineering benchmark; no provider or workflow execution."""

import argparse
import hashlib
import json
import math
import os
import platform
import tempfile
import time
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

import yaml

from server.services.agenthub.database import AgentHubDatabase
from server.services.agenthub.discovery import AgentDiscovery
from server.services.agenthub.metadata import AgentMetadataInput
from server.services.agenthub.registry import AgentRegistry
from server.services.agenthub.reranker import RerankAdapter
from server.services.agenthub.router import (
    CalibratedThreshold,
    SemanticLLMRouter,
    SemanticRouter,
)
from server.services.agenthub.runtime_resolver import RuntimeRefResolver
from server.services.agenthub.thin_workflow import ThinWorkflowValidator

SEED = "agenthub-t43-offline-v1"
DIMENSIONS = 16
MODEL_KEY = "sha256-fake-16d-v1"


def _vector(text: str) -> tuple[float, ...]:
    values = []
    for coordinate in range(DIMENSIONS):
        digest = hashlib.sha256(f"{SEED}:{coordinate}:{text}".encode()).digest()
        values.append(int.from_bytes(digest[:4], "big") / (2 ** 31) - 1)
    return tuple(values)


class OfflineEmbeddingBackend:
    model_key = MODEL_KEY
    dimensions = DIMENSIONS

    def embed(self, texts):
        return [_vector(text) for text in texts]


class TimedEmbeddingBackend:
    model_key = MODEL_KEY
    dimensions = DIMENSIONS

    def __init__(self, backend):
        self.backend = backend
        self.start_ns = None
        self.duration_ms = None

    def embed(self, texts):
        self.start_ns = time.perf_counter_ns()
        result = self.backend.embed(texts)
        self.duration_ms = (time.perf_counter_ns() - self.start_ns) / 1_000_000
        return result


class TimedDiscovery:
    def __init__(self, discovery):
        self.discovery = discovery
        self.duration_ms = None

    def discover(self, task, *, k):
        start = time.perf_counter_ns()
        try:
            return self.discovery.discover(task, k=k)
        finally:
            self.duration_ms = (time.perf_counter_ns() - start) / 1_000_000


class IdentityTransport:
    def complete(self, request):
        return json.dumps({
            "candidate_ids": [item["id"] for item in request["candidates"]],
            "reason_code": "OFFLINE_IDENTITY",
        })


class TimedReranker:
    def __init__(self):
        self.adapter = RerankAdapter(IdentityTransport())
        self.duration_ms = None

    def rerank(self, task, candidates):
        start = time.perf_counter_ns()
        try:
            return self.adapter.rerank(task, candidates)
        finally:
            self.duration_ms = (time.perf_counter_ns() - start) / 1_000_000


def _percentiles(values):
    ordered = sorted(values)
    count = len(ordered)
    return {
        "sample_count": count,
        "p50_ms": ordered[math.ceil(count * 0.5) - 1] if count else None,
        "p95_ms": ordered[math.ceil(count * 0.95) - 1] if count else None,
    }


def _hardware():
    cpu_model = None
    memory_kib = None
    if Path("/proc/cpuinfo").is_file():
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu_model = line.partition(":")[2].strip()
                break
    if Path("/proc/meminfo").is_file():
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                memory_kib = int(line.split()[1])
                break
    return {
        "platform": platform.platform(), "python": platform.python_version(),
        "cpu_model": cpu_model, "logical_cpus": os.cpu_count(),
        "memory_total_kib": memory_kib,
    }


def _catalog(root: Path, size: int):
    workflow_root = root / "workflows"
    workflow_root.mkdir()
    (workflow_root / "thin.yaml").write_text(yaml.safe_dump({
        "version": "0.4.0", "vars": {}, "graph": {
            "id": "synthetic_thin_agent", "start": ["agent"], "end": ["agent"],
            "nodes": [{"id": "agent", "type": "agent", "config": {
                "provider": "openai", "name": "offline-fixture", "role": "Act."
            }}], "edges": [],
        },
    }), encoding="utf-8")
    manifest = {f"workflow://synthetic-{i:03d}/1": "thin.yaml" for i in range(size)}
    (workflow_root / "agenthub_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8",
    )
    registry = AgentRegistry(
        AgentHubDatabase(root / "catalog.db"),
        ThinWorkflowValidator(RuntimeRefResolver(workflow_root)),
        OfflineEmbeddingBackend(),
    )
    started = time.perf_counter_ns()
    for index, runtime_ref in enumerate(manifest):
        registry.register(AgentMetadataInput(
            name=f"Synthetic Agent {index:03d}",
            description=f"Offline synthetic capability {index:03d}",
            capabilities=(f"Process synthetic task {index:03d}",),
            tags=("synthetic",), runtime_ref=runtime_ref,
        ))
    build_ms = (time.perf_counter_ns() - started) / 1_000_000
    return registry, build_ms


def _measure(registry, size: int, *, top_k: int, warmup: int, repeats: int):
    backend = TimedEmbeddingBackend(registry.backend)
    discovery = TimedDiscovery(AgentDiscovery(
        registry.versions, registry.index, registry.validator, backend,
    ))
    threshold = CalibratedThreshold(-1.0, "offline-scan-no-gate-v1")
    semantic = SemanticRouter(discovery, threshold=threshold, top_k=top_k)
    reranker = TimedReranker()
    routers = {"semantic": semantic, "semantic_llm": SemanticLLMRouter(semantic, reranker)}
    tasks = tuple(f"synthetic query {index:02d}" for index in range(12))
    results = {}
    for strategy, router in routers.items():
        rows = []
        errors = []
        for index in range(warmup + repeats * len(tasks)):
            task = tasks[index % len(tasks)]
            backend.start_ns = backend.duration_ms = None
            discovery.duration_ms = reranker.duration_ms = None
            route_started = time.perf_counter_ns()
            decision = router.route(task)
            finished = time.perf_counter_ns()
            if index < warmup:
                continue
            if decision.status == "failed":
                errors.append({"sample": index - warmup, "code": decision.error_code})
            rows.append({
                "query_embedding_ms": backend.duration_ms,
                "discovery_excluding_embedding_ms": (
                    discovery.duration_ms - backend.duration_ms
                    if discovery.duration_ms is not None and backend.duration_ms is not None else None
                ),
                "rerank_ms": reranker.duration_ms,
                "routing_query_to_decision_ms": (
                    (finished - backend.start_ns) / 1_000_000
                    if backend.start_ns is not None else None
                ),
                "routing_full_ms": (finished - route_started) / 1_000_000,
            })
        segments = {key: _percentiles([row[key] for row in rows if row[key] is not None])
                    for key in rows[0]}
        results[strategy] = {
            "catalog_size": size, "request_count": len(rows), "errors": errors,
            "segments": segments,
        }
    return results


def run_offline_benchmark(*, top_k: int = 3, warmup: int = 2, repeats: int = 3):
    if top_k <= 0 or warmup < 0 or repeats <= 0:
        raise ValueError("invalid benchmark parameters")
    report = {
        "label": "deterministic_offline_engineering_only",
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "hardware": _hardware(),
        "config": {
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "catalog_version": "synthetic-v1",
            "catalog_universe_fingerprint": hashlib.sha256(json.dumps([
                {
                    "name": f"Synthetic Agent {index:03d}",
                    "description": f"Offline synthetic capability {index:03d}",
                    "capability": f"Process synthetic task {index:03d}",
                    "runtime_ref": f"workflow://synthetic-{index:03d}/1",
                }
                for index in range(100)
            ], sort_keys=True).encode()).hexdigest(),
            "seed": SEED, "embedding_model_key": MODEL_KEY, "dimensions": DIMENSIONS,
            "catalog_sizes": [10, 50, 100], "top_k": top_k,
            "threshold": -1.0, "threshold_source": "offline-scan-no-gate-v1",
            "warmup_requests_per_strategy": warmup,
            "query_count": 12, "repeats_per_query": repeats,
            "cache": "SQLite/OS caches warm after registration and warmup; no cache flush",
            "catalog_construction": "one version-1 active metadata and indexed vector per Agent; controlled manifest maps all runtime_refs to one statically validated thin workflow",
            "discovery_scope": "status, runtime_ref validation, index load, exact cosine, sort; excludes query embedding",
            "routing_query_to_decision_scope": "starts at query embedding; excludes pre-query status/runtime_ref/index loading",
            "routing_full_scope": "starts before Router.route; includes pre-query discovery work",
            "reranker": "local deterministic identity transport; no LLM request",
        },
        "sizes": [],
    }
    # The workflow is statically validated only; this command never executes it.
    for size in (10, 50, 100):
        with tempfile.TemporaryDirectory(prefix="agenthub-t43-") as directory:
            registry, build_ms = _catalog(Path(directory), size)
            result = _measure(registry, size, top_k=top_k, warmup=warmup, repeats=repeats)
            report["sizes"].append({
                "catalog_size": size, "catalog_and_index_build_ms": build_ms,
                "strategies": result,
            })
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with open(os.devnull, "w", encoding="utf-8") as quiet, redirect_stdout(quiet):
        report = run_offline_benchmark()
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
