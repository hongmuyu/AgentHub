"""Optional OpenAI-compatible embeddings protocol adapter for AgentHub."""

import os
import re
from typing import Sequence
from urllib.parse import urlsplit

import requests

from .embeddings import EmbeddingValidationError, Vector, _validate_identity, _validate_vectors


class EmbeddingConfigurationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EmbeddingTransportError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class OpenAICompatibleEmbeddingBackend:
    """Use a separately configured embeddings endpoint, without chat provider state."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        model_key: str,
        dimensions: int,
        api_key_env: str,
        session: requests.Session | None = None,
    ) -> None:
        _validate_identity(model_key, dimensions)
        if not isinstance(base_url, str) or not base_url:
            raise EmbeddingConfigurationError("INVALID_EMBEDDING_BASE_URL")
        try:
            url = urlsplit(base_url)
        except (TypeError, ValueError):
            raise EmbeddingConfigurationError("INVALID_EMBEDDING_BASE_URL") from None
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise EmbeddingConfigurationError("INVALID_EMBEDDING_BASE_URL")
        if not isinstance(model, str) or not model or model.strip() != model:
            raise EmbeddingConfigurationError("INVALID_EMBEDDING_MODEL")
        if not isinstance(api_key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise EmbeddingConfigurationError("INVALID_EMBEDDING_CREDENTIAL_ENV")
        self.model_key = model_key
        self.dimensions = dimensions
        self._url = base_url.rstrip("/") + "/embeddings"
        self._model = model
        self._api_key_env = api_key_env
        self._session = session

    @classmethod
    def from_environment(
        cls, *, session: requests.Session | None = None
    ) -> "OpenAICompatibleEmbeddingBackend":
        config = os.environ
        keys = (
            "AGENTHUB_EMBEDDING_BASE_URL",
            "AGENTHUB_EMBEDDING_MODEL",
            "AGENTHUB_EMBEDDING_MODEL_KEY",
            "AGENTHUB_EMBEDDING_DIMENSIONS",
            "AGENTHUB_EMBEDDING_API_KEY_ENV",
        )
        if any(not config.get(key) for key in keys):
            raise EmbeddingConfigurationError("EMBEDDING_CONFIGURATION_MISSING")
        try:
            dimensions = int(config["AGENTHUB_EMBEDDING_DIMENSIONS"])
        except ValueError:
            raise EmbeddingConfigurationError("INVALID_DIMENSIONS") from None
        return cls(
            base_url=config["AGENTHUB_EMBEDDING_BASE_URL"],
            model=config["AGENTHUB_EMBEDDING_MODEL"],
            model_key=config["AGENTHUB_EMBEDDING_MODEL_KEY"],
            dimensions=dimensions,
            api_key_env=config["AGENTHUB_EMBEDDING_API_KEY_ENV"],
            session=session,
        )

    def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        if not texts:
            return ()
        if any(not isinstance(text, str) or not text for text in texts):
            raise EmbeddingValidationError("INVALID_TEXTS")
        credential = os.environ.get(self._api_key_env)
        if not credential:
            raise EmbeddingConfigurationError("EMBEDDING_CREDENTIAL_MISSING")
        try:
            post = self._session.post if self._session is not None else requests.post
            reply = post(
                self._url,
                headers={"Authorization": f"Bearer {credential}"},
                json={"input": list(texts), "model": self._model, "encoding_format": "float"},
                timeout=10.0,
            )
        except requests.exceptions.Timeout:
            raise EmbeddingTransportError("EMBEDDING_TIMEOUT") from None
        except requests.exceptions.RequestException:
            raise EmbeddingTransportError("EMBEDDING_TRANSPORT_FAILED") from None
        if reply.status_code in {401, 403}:
            raise EmbeddingTransportError("EMBEDDING_AUTH_FAILED")
        if reply.status_code == 429:
            raise EmbeddingTransportError("EMBEDDING_RATE_LIMITED")
        if reply.status_code >= 500:
            raise EmbeddingTransportError("EMBEDDING_SERVICE_UNAVAILABLE")
        if not 200 <= reply.status_code < 300:
            raise EmbeddingTransportError("EMBEDDING_REQUEST_FAILED")
        try:
            payload = reply.json()
        except (ValueError, UnicodeError):
            raise EmbeddingValidationError("INVALID_EMBEDDING_RESPONSE") from None
        if not isinstance(payload, dict):
            raise EmbeddingValidationError("INVALID_EMBEDDING_RESPONSE")
        if payload.get("model") != self._model:
            raise EmbeddingValidationError("EMBEDDING_MODEL_MISMATCH")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise EmbeddingValidationError("INVALID_EMBEDDING_RESPONSE")
        if len(rows) != len(texts):
            raise EmbeddingValidationError("VECTOR_COUNT_MISMATCH")
        ordered: list[Sequence[float] | None] = [None] * len(texts)
        for row in rows:
            if not isinstance(row, dict):
                raise EmbeddingValidationError("INVALID_EMBEDDING_RESPONSE")
            index = row.get("index")
            if type(index) is not int or not 0 <= index < len(texts) or ordered[index] is not None:
                raise EmbeddingValidationError("INVALID_EMBEDDING_INDEX")
            embedding = row.get("embedding")
            if not isinstance(embedding, list):
                raise EmbeddingValidationError("INVALID_EMBEDDING_RESPONSE")
            ordered[index] = embedding
        return _validate_vectors(ordered, count=len(texts), dimensions=self.dimensions)
