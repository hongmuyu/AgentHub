"""Offline protocol checks for the optional OpenAI-compatible embedding backend."""

import json

import pytest
import requests

from server.services.agenthub.embeddings import EmbeddingValidationError, checked_embed
from server.services.agenthub.openai_embeddings import (
    EmbeddingConfigurationError,
    EmbeddingTransportError,
    OpenAICompatibleEmbeddingBackend,
)


class FakeTransport(requests.adapters.BaseAdapter):
    def __init__(self, handler):
        self.handler = handler

    def send(self, request, **kwargs):
        reply = self.handler(request)
        reply.request = request
        reply.url = request.url
        return reply

    def close(self):
        pass


def fake_session(handler):
    session = requests.Session()
    session.mount("https://", FakeTransport(handler))
    return session


def backend(handler, **overrides):
    options = {
        "base_url": "https://example.invalid/private-token/v1",
        "model": "embedding-model",
        "model_key": "embedding-model@fixture-v1",
        "dimensions": 2,
        "api_key_env": "TEST_AGENTHUB_EMBEDDING_KEY",
        "session": fake_session(handler),
    }
    options.update(overrides)
    return OpenAICompatibleEmbeddingBackend(**options)


def response(rows, *, model="embedding-model"):
    return raw_response(200, json.dumps({"object": "list", "model": model, "data": rows}))


def raw_response(status, body):
    reply = requests.Response()
    reply.status_code = status
    reply._content = body.encode()
    reply.headers["content-type"] = "application/json"
    return reply


def row(index, embedding):
    return {"object": "embedding", "index": index, "embedding": embedding}


def test_request_and_reordered_response_preserve_input_order(monkeypatch):
    monkeypatch.setenv("TEST_AGENTHUB_EMBEDDING_KEY", "secret-key")
    requests = []

    def handler(request):
        requests.append(request)
        return response([row(1, [0, 3]), row(0, [2, 0])])

    result = checked_embed(backend(handler), ["first", "second"])

    assert result.model_key == "embedding-model@fixture-v1"
    assert result.vectors == ((2.0, 0.0), (0.0, 3.0))
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url == "https://example.invalid/private-token/v1/embeddings"
    assert requests[0].headers["Authorization"] == "Bearer secret-key"
    assert json.loads(requests[0].body) == {
        "input": ["first", "second"],
        "model": "embedding-model",
        "encoding_format": "float",
    }


def test_empty_batch_does_not_call_transport(monkeypatch):
    monkeypatch.setenv("TEST_AGENTHUB_EMBEDDING_KEY", "secret-key")

    def handler(request):
        raise AssertionError("unexpected request")

    assert checked_embed(backend(handler), []).vectors == ()


@pytest.mark.parametrize(
    "payload,code",
    [
        ([row(0, [1, 0])], "VECTOR_COUNT_MISMATCH"),
        ([row(0, [1, 0]), row(0, [0, 1])], "INVALID_EMBEDDING_INDEX"),
        ([row(0, [1, 0]), row(2, [0, 1])], "INVALID_EMBEDDING_INDEX"),
        ([row(True, [1, 0]), row(1, [0, 1])], "INVALID_EMBEDDING_INDEX"),
        ([row(0, [1]), row(1, [0, 1])], "INVALID_VECTOR_DIMENSIONS"),
        ([row(0, [float("nan"), 1]), row(1, [0, 1])], "NON_FINITE_VECTOR"),
        ([row(0, [1, 0]), row(1, [0, 0])], "ZERO_VECTOR"),
    ],
)
def test_invalid_batch_is_rejected(monkeypatch, payload, code):
    monkeypatch.setenv("TEST_AGENTHUB_EMBEDDING_KEY", "secret-key")

    with pytest.raises(EmbeddingValidationError) as error:
        checked_embed(backend(lambda request: response(payload)), ["first", "second"])
    assert error.value.code == code


@pytest.mark.parametrize(
    "reply,code",
    [
        (lambda: response([row(0, [1, 0])], model="other-model"), "EMBEDDING_MODEL_MISMATCH"),
        (lambda: raw_response(200, '{"data":"bad","model":"embedding-model"}'), "INVALID_EMBEDDING_RESPONSE"),
        (lambda: raw_response(200, "not-json"), "INVALID_EMBEDDING_RESPONSE"),
        (lambda: raw_response(401, "secret-key private-token"), "EMBEDDING_AUTH_FAILED"),
        (lambda: raw_response(503, "secret-key private-token"), "EMBEDDING_SERVICE_UNAVAILABLE"),
    ],
)
def test_model_response_and_http_failures_are_safe(monkeypatch, reply, code):
    monkeypatch.setenv("TEST_AGENTHUB_EMBEDDING_KEY", "secret-key")

    with pytest.raises((EmbeddingValidationError, EmbeddingTransportError)) as error:
        checked_embed(backend(lambda request: reply()), ["first"])
    assert error.value.code == code
    assert str(error.value) == code
    assert "secret-key" not in str(error.value)
    assert "private-token" not in str(error.value)


def test_timeout_is_classified_without_leaking_url_or_key(monkeypatch):
    monkeypatch.setenv("TEST_AGENTHUB_EMBEDDING_KEY", "secret-key")

    def handler(request):
        raise requests.exceptions.Timeout("secret-key private-token")

    with pytest.raises(EmbeddingTransportError) as error:
        checked_embed(backend(handler), ["first"])
    assert error.value.code == "EMBEDDING_TIMEOUT"
    assert str(error.value) == "EMBEDDING_TIMEOUT"
    assert error.value.__cause__ is None


def test_transport_does_not_log_sensitive_url_or_response(monkeypatch, caplog):
    monkeypatch.setenv("TEST_AGENTHUB_EMBEDDING_KEY", "secret-key")
    caplog.set_level("INFO")

    with pytest.raises(EmbeddingTransportError):
        checked_embed(
            backend(lambda request: raw_response(503, "secret-key private-token")),
            ["task"],
        )
    assert "secret-key" not in caplog.text
    assert "private-token" not in caplog.text


def test_configuration_is_separate_and_missing_key_is_safe(monkeypatch):
    monkeypatch.delenv("TEST_AGENTHUB_EMBEDDING_KEY", raising=False)
    monkeypatch.setenv("API_KEY", "chat-secret")

    with pytest.raises(EmbeddingConfigurationError) as error:
        checked_embed(backend(lambda request: pytest.fail("unexpected request")), ["first"])
    assert error.value.code == "EMBEDDING_CREDENTIAL_MISSING"
    assert "chat-secret" not in str(error.value)


def test_from_environment_uses_only_embedding_keys(monkeypatch):
    monkeypatch.setenv("AGENTHUB_EMBEDDING_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AGENTHUB_EMBEDDING_MODEL", "embedding-model")
    monkeypatch.setenv("AGENTHUB_EMBEDDING_MODEL_KEY", "model-space-v1")
    monkeypatch.setenv("AGENTHUB_EMBEDDING_DIMENSIONS", "2")
    monkeypatch.setenv("AGENTHUB_EMBEDDING_API_KEY_ENV", "TEST_AGENTHUB_EMBEDDING_KEY")
    monkeypatch.setenv("TEST_AGENTHUB_EMBEDDING_KEY", "secret-key")

    session = fake_session(lambda request: response([row(0, [1, 0])]))
    configured = OpenAICompatibleEmbeddingBackend.from_environment(session=session)
    assert checked_embed(configured, ["task"]).vectors == ((1.0, 0.0),)
    assert configured.model_key == "model-space-v1"


def test_missing_environment_configuration_is_safe(monkeypatch):
    monkeypatch.delenv("AGENTHUB_EMBEDDING_MODEL", raising=False)
    with pytest.raises(EmbeddingConfigurationError) as error:
        OpenAICompatibleEmbeddingBackend.from_environment()
    assert error.value.code == "EMBEDDING_CONFIGURATION_MISSING"


def test_invalid_model_key_is_rejected():
    with pytest.raises(EmbeddingValidationError) as error:
        backend(lambda request: pytest.fail("unexpected request"), model_key="")
    assert error.value.code == "INVALID_MODEL_KEY"


def test_url_credentials_are_rejected_without_echoing_them():
    with pytest.raises(EmbeddingConfigurationError) as error:
        backend(
            lambda request: pytest.fail("unexpected request"),
            base_url="https://private-user:private-key@example.invalid/v1",
        )
    assert error.value.code == "INVALID_EMBEDDING_BASE_URL"
    assert "private" not in str(error.value)
