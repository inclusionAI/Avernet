"""Deterministic offline vectors for local plumbing checks, not semantic quality."""

import hashlib
import math


class DemoEmbedding:
    """Deterministic character features for plumbing demos, not semantic quality."""

    dimension = 256

    def __init__(self, dimension=256):
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        text = text.strip().lower()
        for width in (1, 2):
            for position in range(len(text) - width + 1):
                token = text[position : position + width].encode()
                bucket = int.from_bytes(hashlib.sha256(token).digest()[:4], "big")
                vector[bucket % self.dimension] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector
