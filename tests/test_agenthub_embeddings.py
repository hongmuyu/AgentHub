"""Offline embedding contract and deterministic fixture backend."""

import socket
import sys

import pytest

from server.services.agenthub.embeddings import (
    EmbeddingValidationError,
    FakeEmbeddingBackend,
    checked_embed,
)


class StubBackend:
    model_key = "stub-model"
    dimensions = 2

    def __init__(self, vectors=None, error=None):
        self.vectors = vectors
        self.error = error
        self.calls = []

    def embed(self, texts):
        self.calls.append(tuple(texts))
        if self.error is not None:
            raise self.error
        return self.vectors


def test_fake_preserves_batch_order_and_repeats_deterministically():
    known = {"alpha": [1, 0], "beta": [0, 1]}
    backend = FakeEmbeddingBackend(known, model_key="fixture-v1", dimensions=2)
    known["alpha"][0] = 99

    first = checked_embed(backend, ["beta", "alpha", "beta"])
    second = checked_embed(backend, ["beta", "alpha", "beta"])

    assert first == second
    assert first.model_key == "fixture-v1"
    assert first.dimensions == 2
    assert first.vectors == ((0.0, 1.0), (1.0, 0.0), (0.0, 1.0))
    assert backend.embed(["alpha", "beta"]) == [(1.0, 0.0), (0.0, 1.0)]


def test_empty_input_returns_empty_batch():
    backend = FakeEmbeddingBackend({}, model_key="fixture-empty", dimensions=2)

    assert backend.embed([]) == []
    assert checked_embed(backend, []).vectors == ()


def test_independent_backend_uses_same_contract_and_receives_ordered_inputs():
    backend = StubBackend([(0, 2), (3, 0)])

    batch = checked_embed(backend, ["second", "first"])

    assert backend.calls == [("second", "first")]
    assert batch.model_key == "stub-model"
    assert batch.dimensions == 2
    assert batch.vectors == ((0.0, 2.0), (3.0, 0.0))


def test_text_iterator_is_consumed_once_in_order():
    backend = StubBackend([(1.0, 0.0), (0.0, 1.0)])

    batch = checked_embed(backend, (text for text in ["first", "second"]))

    assert backend.calls == [("first", "second")]
    assert batch.vectors == ((1.0, 0.0), (0.0, 1.0))


@pytest.mark.parametrize(
    "model_key,dimensions,code",
    [("", 2, "INVALID_MODEL_KEY"), ("fake", 0, "INVALID_DIMENSIONS"),
     ("fake", True, "INVALID_DIMENSIONS")],
)
def test_fake_rejects_invalid_model_identity_or_dimension(model_key, dimensions, code):
    with pytest.raises(EmbeddingValidationError) as error:
        FakeEmbeddingBackend({}, model_key=model_key, dimensions=dimensions)
    assert error.value.code == code


@pytest.mark.parametrize(
    "vector,code",
    [
        ((1.0,), "INVALID_VECTOR_DIMENSIONS"),
        ((float("nan"), 1.0), "NON_FINITE_VECTOR"),
        ((float("inf"), 1.0), "NON_FINITE_VECTOR"),
        ((sys.float_info.max, sys.float_info.max), "NON_FINITE_VECTOR"),
        ((0.0, 0.0), "ZERO_VECTOR"),
        ((True, 1.0), "INVALID_VECTOR_VALUE"),
    ],
)
def test_fake_rejects_bad_injected_vectors(vector, code):
    with pytest.raises(EmbeddingValidationError) as error:
        FakeEmbeddingBackend({"task": vector}, model_key="fixture", dimensions=2)
    assert error.value.code == code


@pytest.mark.parametrize(
    "vectors,code",
    [
        ([], "VECTOR_COUNT_MISMATCH"),
        ([(1.0,)], "INVALID_VECTOR_DIMENSIONS"),
        ([(float("-inf"), 1.0)], "NON_FINITE_VECTOR"),
        ([(0.0, 0.0)], "ZERO_VECTOR"),
    ],
)
def test_checked_embed_rejects_invalid_backend_output(vectors, code):
    with pytest.raises(EmbeddingValidationError) as error:
        checked_embed(StubBackend(vectors), ["task"])
    assert error.value.code == code


def test_backend_error_propagates_unchanged():
    failure = RuntimeError("backend unavailable")
    with pytest.raises(RuntimeError) as error:
        checked_embed(StubBackend(error=failure), ["task"])
    assert error.value is failure


def test_fake_does_not_open_network_connections(monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    backend = FakeEmbeddingBackend({"task": (1.0, 0.0)}, model_key="fixture", dimensions=2)

    assert checked_embed(backend, ["task"]).vectors == ((1.0, 0.0),)


def test_unknown_fake_text_has_safe_error():
    backend = FakeEmbeddingBackend({"known": (1.0, 0.0)}, model_key="fixture", dimensions=2)
    with pytest.raises(KeyError) as error:
        backend.embed(["private task content"])
    assert "private task content" not in str(error.value)
