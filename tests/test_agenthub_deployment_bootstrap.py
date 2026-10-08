"""T46: standard app startup wires AgentHub only with matching configuration."""

import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import runtime  # noqa: F401  # Initialize the inherited runtime/check import cycle first.
from server.bootstrap import init_app
from server.services.agenthub import app_setup

EMBEDDING_KEYS = (
    "AGENTHUB_EMBEDDING_BASE_URL",
    "AGENTHUB_EMBEDDING_MODEL",
    "AGENTHUB_EMBEDDING_MODEL_KEY",
    "AGENTHUB_EMBEDDING_DIMENSIONS",
    "AGENTHUB_EMBEDDING_API_KEY_ENV",
)


def _digest(values):
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def _embedding_config(monkeypatch, tmp_path):
    values = (
        "https://example.invalid/v1", "fixture-embedding-model",
        "qwen-text-embedding-v4-1024", "1024", "T46_SYNTHETIC_KEY",
    )
    for key, value in zip(EMBEDDING_KEYS, values, strict=True):
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("T46_SYNTHETIC_KEY", "fixture-only")
    artifact = tmp_path / "calibration.json"
    artifact.write_text(json.dumps({
        "status": "frozen_before_test",
        "frozen_config": {"top_k": 5, "threshold": 0.5, "threshold_source": "fixture"},
        "provenance": {
            "embedding_model_key": values[2], "embedding_dimensions": 1024,
            "embedding_config_sha256": _digest((values[0], values[1], values[2], 1024)),
            "rerank_config_sha256": _digest(("https://example.invalid/v1", "deepseek-flash")),
        },
    }))
    monkeypatch.setattr(app_setup, "_CALIBRATION_FILE", artifact)


def test_standard_app_wires_registry_task_query_and_metrics_without_provider_call(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    _embedding_config(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENTHUB_RERANK_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AGENTHUB_RERANK_API_KEY_ENV", "T46_SYNTHETIC_KEY")
    monkeypatch.setenv("AGENTHUB_RERANK_MODEL", "deepseek-flash")
    app = FastAPI()
    init_app(app)

    with TestClient(app) as client:
        agents = client.get("/api/agenthub/agents")
        task = client.post("/api/agenthub/tasks", json={
            "task": "fixture", "session_id": "missing", "routing_strategy": "semantic",
        })
        metrics = client.get("/api/agenthub/metrics")
        legacy = client.get("/api/workflows")

    assert agents.status_code == 200
    assert agents.json()["agents"] == []
    assert task.status_code == 422
    assert task.json()["detail"]["code"] == "SESSION_NOT_FOUND"
    assert metrics.status_code == legacy.status_code == 200
    assert set(app.state.agenthub_task_service.routers) == {"semantic", "semantic_llm"}
    assert app.state.agenthub_task_service.routers["semantic"].top_k == 5


def test_unconfigured_agenthub_keeps_legacy_app_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in EMBEDDING_KEYS:
        monkeypatch.delenv(key, raising=False)
    app = FastAPI()
    init_app(app)

    with TestClient(app) as client:
        legacy = client.get("/api/workflows")
        agents = client.get("/api/agenthub/agents")

    assert legacy.status_code == 200
    assert agents.status_code == 503
    assert agents.json()["detail"]["code"] == "REGISTRY_NOT_CONFIGURED"


def test_calibration_model_mismatch_does_not_wire_wrong_router(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _embedding_config(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENTHUB_EMBEDDING_MODEL_KEY", "different-model")

    with pytest.raises(ValueError, match="AGENTHUB_CALIBRATION_MISMATCH"):
        init_app(FastAPI())


def test_calibration_endpoint_mismatch_does_not_wire_wrong_router(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _embedding_config(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENTHUB_EMBEDDING_BASE_URL", "https://other.invalid/v1")

    with pytest.raises(ValueError, match="AGENTHUB_CALIBRATION_MISMATCH"):
        init_app(FastAPI())


def test_calibration_rerank_mismatch_does_not_wire_wrong_router(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _embedding_config(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENTHUB_RERANK_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AGENTHUB_RERANK_API_KEY_ENV", "T46_SYNTHETIC_KEY")
    monkeypatch.setenv("AGENTHUB_RERANK_MODEL", "different-reranker")

    with pytest.raises(ValueError, match="AGENTHUB_CALIBRATION_MISMATCH"):
        init_app(FastAPI())
