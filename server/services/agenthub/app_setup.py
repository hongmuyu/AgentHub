"""Wire the existing AgentHub services into a configured Web deployment."""

import hashlib
import json
import os
from pathlib import Path

from fastapi import FastAPI

from server.services.websocket_manager import WebSocketManager

from .database import AgentHubDatabase
from .discovery import AgentDiscovery
from .metrics import MetricsService
from .openai_embeddings import OpenAICompatibleEmbeddingBackend
from .openai_rerank import OpenAICompatibleRerankTransport
from .registry import AgentRegistry
from .reranker import RerankAdapter
from .router import CalibratedThreshold, SemanticLLMRouter, SemanticRouter
from .run_query import RunQueryService
from .runtime_resolver import RuntimeRefResolver
from .task_service import TaskSubmissionService
from .thin_workflow import ThinWorkflowValidator
from .workflow_dispatcher import AgentHubWorkflowDispatcher

_EMBEDDING_KEYS = (
    "AGENTHUB_EMBEDDING_BASE_URL", "AGENTHUB_EMBEDDING_MODEL",
    "AGENTHUB_EMBEDDING_MODEL_KEY", "AGENTHUB_EMBEDDING_DIMENSIONS",
    "AGENTHUB_EMBEDDING_API_KEY_ENV",
)
_CALIBRATION_FILE = (
    Path(__file__).resolve().parents[3] / "evaluation" / "t43_live_calibration_2026-10-08.json"
)


def _frozen_gate(backend: OpenAICompatibleEmbeddingBackend) -> tuple[int, CalibratedThreshold]:
    try:
        artifact = json.loads(_CALIBRATION_FILE.read_text(encoding="utf-8"))
        frozen = artifact["frozen_config"]
        provenance = artifact["provenance"]
        if artifact["status"] != "frozen_before_test":
            raise ValueError("not a frozen calibration")
        if (
            provenance["embedding_model_key"] != backend.model_key
            or provenance["embedding_dimensions"] != backend.dimensions
            or provenance["embedding_config_sha256"] != _config_digest((
                os.environ["AGENTHUB_EMBEDDING_BASE_URL"],
                os.environ["AGENTHUB_EMBEDDING_MODEL"],
                backend.model_key, backend.dimensions,
            ))
        ):
            raise ValueError("AGENTHUB_CALIBRATION_MISMATCH")
        top_k = frozen["top_k"]
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("invalid top-k")
        threshold = CalibratedThreshold(frozen["threshold"], frozen["threshold_source"])
        return top_k, threshold
    except ValueError as exc:
        if str(exc) == "AGENTHUB_CALIBRATION_MISMATCH":
            raise
        raise ValueError("AGENTHUB_CALIBRATION_INVALID") from None
    except (OSError, KeyError, TypeError):
        raise ValueError("AGENTHUB_CALIBRATION_INVALID") from None


def _config_digest(values: tuple[object, ...]) -> str:
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def configure_agenthub(app: FastAPI, manager: WebSocketManager) -> None:
    """Keep ordinary ChatDev startup untouched when AgentHub is not configured."""
    if not any(os.environ.get(key) for key in _EMBEDDING_KEYS):
        return

    backend = OpenAICompatibleEmbeddingBackend.from_environment()
    top_k, threshold = _frozen_gate(backend)
    database = AgentHubDatabase()
    validator = ThinWorkflowValidator(RuntimeRefResolver())
    registry = AgentRegistry(database, validator, backend)
    semantic = SemanticRouter(
        AgentDiscovery(registry.versions, registry.index, validator, backend),
        threshold=threshold, top_k=top_k,
    )
    routers = {"semantic": semantic}
    rerank_model_key = None
    if os.environ.get("AGENTHUB_RERANK_BASE_URL") or os.environ.get("AGENTHUB_RERANK_API_KEY_ENV"):
        transport = OpenAICompatibleRerankTransport.from_environment()
        artifact = json.loads(_CALIBRATION_FILE.read_text(encoding="utf-8"))
        if artifact["provenance"]["rerank_config_sha256"] != _config_digest((
            os.environ["AGENTHUB_RERANK_BASE_URL"], transport.model_key,
        )):
            raise ValueError("AGENTHUB_CALIBRATION_MISMATCH")
        routers["semantic_llm"] = SemanticLLMRouter(semantic, RerankAdapter(transport))
        rerank_model_key = transport.model_key

    dispatcher = AgentHubWorkflowDispatcher(database, manager, validator)
    app.state.agenthub_registry = registry
    app.state.agenthub_task_service = TaskSubmissionService(
        database, manager, routers, validator, dispatcher,
        embedding_model_key=backend.model_key, rerank_model_key=rerank_model_key,
    )
    app.state.agenthub_run_query_service = RunQueryService(database)
    app.state.agenthub_metrics_service = MetricsService(database)
