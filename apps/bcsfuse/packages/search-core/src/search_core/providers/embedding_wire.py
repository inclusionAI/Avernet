"""Little-endian float32 embedding response wire format.

The protocol is negotiated per HTTP response. Requests still use the existing
OpenAI-compatible JSON body, and clients always advertise JSON as a fallback.
"""


from __future__ import annotations

import array
import math
import struct
import sys
from collections.abc import Sequence

BINARY_CONTENT_TYPE = "application/x-bcsfuse-embedding-v1"
EMBEDDING_ACCEPT_HEADER = (
    f"{BINARY_CONTENT_TYPE}, application/json;q=0.9"
)

_MAGIC = b"BCFE"
_VERSION = 1
_FLOAT32_LITTLE_ENDIAN = 1
_HEADER = struct.Struct("<4sBBII")


class EmbeddingWireError(ValueError):
    """A declared binary embedding response violates the wire contract."""


class EmbeddingWireDimensionError(EmbeddingWireError):
    """The binary response declared a dimension outside the configured contract."""

    def __init__(
        self,
        message: str,
        *,
        expected_dimension: int,
        actual_dimension: int,
    ):
        super().__init__(message)
        self.expected_dimension = expected_dimension
        self.actual_dimension = actual_dimension


def encode_embedding_vectors(
    vectors: Sequence[Sequence[float]],
    *,
    dimension: int,
) -> bytes:
    """Encode one or more bounded vectors for tests and public clients.

    The production HTTP client only needs to decode this format. Keeping the
    encoder in the shared package lets model-gateway owners use one tested
    implementation instead of inventing a private wire format.
    """

    if dimension <= 0:
        raise ValueError("dimension must be positive")
    if not vectors:
        raise ValueError("at least one vector is required")

    chunks = [_HEADER.pack(_MAGIC, _VERSION, _FLOAT32_LITTLE_ENDIAN, dimension, len(vectors))]
    for vector in vectors:
        if len(vector) != dimension:
            raise ValueError("all vectors must match the declared dimension")
        if not all(math.isfinite(float(value)) for value in vector):
            raise ValueError("all vector values must be finite")
        chunks.append(_float32_bytes(vector))
    return b"".join(chunks)


def decode_embedding_vectors(
    payload: bytes,
    *,
    expected_count: int,
    dimension: int,
) -> list[list[float]]:
    """Validate and decode a little-endian float32 embedding response."""

    if not isinstance(payload, (bytes, bytearray)) or len(payload) < _HEADER.size:
        raise EmbeddingWireError(
            "Embedding binary response payload length is invalid or too short"
        )

    magic, version, dtype, encoded_dimension, count = _HEADER.unpack_from(payload)
    if magic != _MAGIC:
        raise EmbeddingWireError("Embedding binary response has invalid magic")
    if version != _VERSION:
        raise EmbeddingWireError(
            "Embedding binary response uses an unsupported protocol version"
        )
    if dtype != _FLOAT32_LITTLE_ENDIAN:
        raise EmbeddingWireError("Embedding binary response uses an unsupported dtype")
    if encoded_dimension != dimension:
        raise EmbeddingWireDimensionError(
            "Embedding binary response dimension does not match the configured model dimension",
            expected_dimension=dimension,
            actual_dimension=encoded_dimension,
        )
    if count != expected_count:
        raise EmbeddingWireError(
            "Embedding binary response count does not match the requested input count"
        )

    value_bytes = dimension * 4
    expected_length = _HEADER.size + count * value_bytes
    if len(payload) != expected_length:
        raise EmbeddingWireError(
            "Embedding binary response payload length is invalid or too short"
        )

    vectors: list[list[float]] = []
    offset = _HEADER.size
    for _ in range(count):
        raw = bytes(payload[offset : offset + value_bytes])
        vectors.append(_float32_values(raw, dimension))
        offset += value_bytes
    return vectors


def _float32_bytes(vector: Sequence[float]) -> bytes:
    if sys.byteorder == "little":
        return array.array("f", (float(value) for value in vector)).tobytes()
    return struct.pack(f"<{len(vector)}f", *(float(value) for value in vector))


def _float32_values(raw: bytes, dimension: int) -> list[float]:
    if sys.byteorder == "little":
        values = array.array("f")
        values.frombytes(raw)
        result = values.tolist()
    else:
        result = list(struct.unpack(f"<{dimension}f", raw))
    if not all(math.isfinite(value) for value in result):
        raise EmbeddingWireError("Embedding binary response contains non-finite values")
    return result


__all__ = [
    "BINARY_CONTENT_TYPE",
    "EMBEDDING_ACCEPT_HEADER",
    "EmbeddingWireError",
    "decode_embedding_vectors",
    "encode_embedding_vectors",
    "EmbeddingWireDimensionError",
]
