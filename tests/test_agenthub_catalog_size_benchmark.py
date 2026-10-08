"""Controlled timing checks for the offline scale benchmark."""

from unittest.mock import patch

from evaluation.catalog_size_benchmark import (
    OfflineEmbeddingBackend,
    TimedDiscovery,
    TimedEmbeddingBackend,
    TimedReranker,
    _percentiles,
    run_offline_benchmark,
)


def test_segment_boundary_excludes_embedding_from_discovery():
    backend = TimedEmbeddingBackend(OfflineEmbeddingBackend())

    class Source:
        def discover(self, task, *, k):
            assert len(backend.embed((task,))) == 1
            return ()

    discovery = TimedDiscovery(Source())
    with patch("evaluation.catalog_size_benchmark.time.perf_counter_ns",
               side_effect=(0, 10_000_000, 30_000_000, 60_000_000)):
        assert discovery.discover("query", k=1) == ()
    assert backend.duration_ms == 20
    assert discovery.duration_ms - backend.duration_ms == 40


def test_nearest_rank_percentiles_and_empty_samples():
    assert _percentiles([50, 10, 40, 20, 30]) == {
        "sample_count": 5, "p50_ms": 30, "p95_ms": 50,
    }
    assert _percentiles([]) == {"sample_count": 0, "p50_ms": None, "p95_ms": None}


def test_rerank_segment_boundary():
    reranker = TimedReranker()

    class Adapter:
        def rerank(self, task, candidates):
            return "selected"

    reranker.adapter = Adapter()
    with patch("evaluation.catalog_size_benchmark.time.perf_counter_ns",
               side_effect=(100_000_000, 135_000_000)):
        assert reranker.rerank("query", ()) == "selected"
    assert reranker.duration_ms == 35


def test_all_three_catalog_sizes_are_measured_without_live_provider():
    report = run_offline_benchmark(warmup=1, repeats=1)
    assert report["label"] == "deterministic_offline_engineering_only"
    assert [row["catalog_size"] for row in report["sizes"]] == [10, 50, 100]
    for size in report["sizes"]:
        assert size["catalog_and_index_build_ms"] > 0
        for strategy in ("semantic", "semantic_llm"):
            result = size["strategies"][strategy]
            assert result["request_count"] == 12
            assert result["errors"] == []
            assert result["segments"]["routing_query_to_decision_ms"]["sample_count"] == 12
            assert result["segments"]["routing_full_ms"]["sample_count"] == 12
            assert result["segments"]["query_embedding_ms"]["sample_count"] == 12
            assert result["segments"]["discovery_excluding_embedding_ms"]["sample_count"] == 12
            assert result["segments"]["rerank_ms"]["sample_count"] == (12 if strategy == "semantic_llm" else 0)
