"""HTTP admission, search deadlines and optional policy settings."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchSettings:
    max_inflight_requests: int = 32
    embedding_timeout: float = 1.0
    index_timeout: float = 0.5
    reranker_timeout: float = 1.5
    request_timeout: float = 4.0
    document_recall_limit: int = 60
    vector_min_score: float | None = None
    keyword_min_score: float | None = None
    result_cache_max_entries: int = 1024
    result_cache_max_bytes: int = 16 * 1024 * 1024
    embedding_cache_max_entries: int = 512
    embedding_cache_max_bytes: int = 64 * 1024 * 1024

    def __post_init__(self):
        if (
            type(self.document_recall_limit) is not int
            or not 30 <= self.document_recall_limit <= 1000
        ):
            raise ValueError("document_recall_limit must be between 30 and 1000")
        for value in (self.vector_min_score, self.keyword_min_score):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("recall minimum scores must be finite numbers or None")
        for name in (
            "result_cache_max_entries",
            "result_cache_max_bytes",
            "embedding_cache_max_entries",
            "embedding_cache_max_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (
            type(self.max_inflight_requests) is not int
            or self.max_inflight_requests < 0
        ):
            raise ValueError("max_inflight_requests must be a nonnegative integer")
        for name in (
            "embedding_timeout",
            "index_timeout",
            "reranker_timeout",
            "request_timeout",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


DEFAULT_SEARCH = SearchSettings()
