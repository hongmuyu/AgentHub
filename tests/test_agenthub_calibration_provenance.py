"""Calibration provenance must describe the gate actually selected, without live calls."""

import json
from pathlib import Path

import pytest

from evaluation import live_routing_benchmark as live
from evaluation.catalog_size_benchmark import OfflineEmbeddingBackend

ROOT = Path(__file__).resolve().parents[1]
SELECTION_RULE = (
    "maximize Top-1 Accuracy + Reject Accuracy; then Top-K Recall; "
    "then smaller K; then higher threshold"
)


def _grid_winner(report):
    # Use only saved calibration fractions, never test labels or measurements.
    return max(report["gate_grid"], key=lambda row: (
        row["quality"]["top1"]["value"] + row["quality"]["reject"]["value"],
        row["quality"]["top_k"]["value"],
        -row["top_k"],
        row["threshold"],
    ))


@pytest.mark.parametrize("filename,config_id", [
    ("t43_live_calibration_2026-10-08.json",
     "eaf560784ee8eb9eb4d00f51bc200bc514479caab129e56bf8f481cbf1cd1b4b"),
    ("t43_live_calibration_attempt1_2026-10-08.json",
     "0111662eee1972a8e0c223e53e3d1a5530dfac84a9b463ac918d6df4a1e3957a"),
])
def test_saved_calibration_rule_explains_original_frozen_gate(filename, config_id):
    report = json.loads((ROOT / "evaluation" / filename).read_text())
    winner = _grid_winner(report)
    assert (winner["top_k"], winner["threshold"], winner["config_id"]) == (
        5, 0.35727615782291194, config_id,
    )
    assert report["frozen_config"]["calibration_config_id"] == winner["config_id"]
    assert report["frozen_config"]["top_k"] == winner["top_k"]
    assert report["frozen_config"]["threshold"] == winner["threshold"]
    assert report["frozen_config"]["selected_calibration_quality"] == winner["quality"]
    assert report["gate_selection_rule"] == SELECTION_RULE


def test_generator_records_rule_matching_actual_calibration_selection(tmp_path, monkeypatch):
    class UnusedTransport:
        model_key = "offline-test"

        def complete(self, request):
            pytest.fail("reranking must not run during calibration")

    class StopBeforeTest(Exception):
        pass

    def stop_before_test(*args, **kwargs):
        raise StopBeforeTest

    # Replace only external configuration/providers; run the real Registry,
    # calibration Router, grid sweep, selector and JSON writer in temporary storage.
    monkeypatch.setattr(live, "load_dotenv_file", lambda: None)
    monkeypatch.setattr(live, "_require_provider_configuration", lambda: None)
    monkeypatch.setattr(live.OpenAICompatibleEmbeddingBackend, "from_environment",
                        lambda: OfflineEmbeddingBackend())
    monkeypatch.setattr(live.OpenAICompatibleRerankTransport, "from_environment",
                        lambda: UnusedTransport())
    monkeypatch.setattr(live, "run_frozen_test", stop_before_test)
    monkeypatch.setenv("AGENTHUB_EMBEDDING_BASE_URL", "https://example.invalid/embedding")
    monkeypatch.setenv("AGENTHUB_EMBEDDING_MODEL", "offline-test")
    monkeypatch.setenv("AGENTHUB_RERANK_BASE_URL", "https://example.invalid/rerank")

    with pytest.raises(StopBeforeTest):
        live.run_live_evaluation(tmp_path)

    (artifact,) = tmp_path.glob("t43_live_calibration_*.json")
    report = json.loads(artifact.read_text())
    assert report["status"] == "frozen_before_test"
    assert report["calibration_baseline_run"]["config"]["split"] == "calibration"
    winner = _grid_winner(report)
    assert report["frozen_config"]["calibration_config_id"] == winner["config_id"]
    assert report["gate_selection_rule"] == SELECTION_RULE
    assert not list(tmp_path.glob("t43_live_test_*.json"))
