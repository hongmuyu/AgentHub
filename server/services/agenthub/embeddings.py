"""Provider-independent embedding contract and offline test vectors."""

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol, Sequence


Vector = tuple[float, ...]


class EmbeddingBackend(Protocol):
    model_key: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return one vector per input text in the same order."""


class EmbeddingValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class EmbeddingBatch:
    model_key: str
    dimensions: int
    vectors: tuple[Vector, ...]


def _validate_identity(model_key: str, dimensions: int) -> None:
    if not isinstance(model_key, str) or not model_key or model_key.strip() != model_key:
        raise EmbeddingValidationError("INVALID_MODEL_KEY")
    if type(dimensions) is not int or dimensions <= 0:
        raise EmbeddingValidationError("INVALID_DIMENSIONS")


def _validate_vectors(
    vectors: Sequence[Sequence[float]], *, count: int, dimensions: int
) -> tuple[Vector, ...]:
    try:
        rows = tuple(vectors)
    except TypeError:
        raise EmbeddingValidationError("VECTOR_COUNT_MISMATCH") from None
    if len(rows) != count:
        raise EmbeddingValidationError("VECTOR_COUNT_MISMATCH")

    validated: list[Vector] = []
    for row in rows:
        try:
            coordinates = tuple(row)
        except TypeError:
            raise EmbeddingValidationError("INVALID_VECTOR_DIMENSIONS") from None
        if len(coordinates) != dimensions:
            raise EmbeddingValidationError("INVALID_VECTOR_DIMENSIONS")
        values: list[float] = []
        for coordinate in coordinates:
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                raise EmbeddingValidationError("INVALID_VECTOR_VALUE")
            try:
                value = float(coordinate)
            except OverflowError:
                raise EmbeddingValidationError("NON_FINITE_VECTOR") from None
            if not math.isfinite(value):
                raise EmbeddingValidationError("NON_FINITE_VECTOR")
            values.append(value)
        norm = math.hypot(*values)
        if not math.isfinite(norm):
            raise EmbeddingValidationError("NON_FINITE_VECTOR")
        if norm == 0.0:
            raise EmbeddingValidationError("ZERO_VECTOR")
        validated.append(tuple(values))
    return tuple(validated)


def checked_embed(backend: EmbeddingBackend, texts: Iterable[str]) -> EmbeddingBatch:
    """Call a backend once and validate its ordered batch for cosine use."""
    model_key, dimensions = backend.model_key, backend.dimensions
    _validate_identity(model_key, dimensions)
    if isinstance(texts, (str, bytes)):
        raise EmbeddingValidationError("INVALID_TEXTS")
    ordered_texts = tuple(texts)
    if any(not isinstance(text, str) for text in ordered_texts):
        raise EmbeddingValidationError("INVALID_TEXTS")
    vectors = backend.embed(ordered_texts)
    return EmbeddingBatch(
        model_key=model_key,
        dimensions=dimensions,
        vectors=_validate_vectors(vectors, count=len(ordered_texts), dimensions=dimensions),
    )


class FakeEmbeddingBackend:
    """Return only explicitly supplied vectors; infer no semantic similarity."""

    def __init__(
        self, vectors: Mapping[str, Sequence[float]], *, model_key: str, dimensions: int
    ) -> None:
        _validate_identity(model_key, dimensions)
        if any(not isinstance(text, str) for text in vectors):
            raise EmbeddingValidationError("INVALID_TEXTS")
        keys = tuple(vectors)
        values = _validate_vectors(
            tuple(vectors[text] for text in keys), count=len(keys), dimensions=dimensions
        )
        self.model_key = model_key
        self.dimensions = dimensions
        self._vectors = dict(zip(keys, values))

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        try:
            return [self._vectors[text] for text in texts]
        except KeyError:
            raise KeyError("fake embedding text is not configured") from None
