"""Bounded literal fallback used only when both retrieval branches are down."""

from __future__ import annotations

import re
import unicodedata

from search_core.plugin_api.vector_point import VectorPoint
from search_core.plugin_api.vector_search_hit import VectorSearchHit


def _normalised_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


_QUERY_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]+")


def _query_tokens(query: str) -> set[str]:
    normalized = _normalised_text(query)
    tokens = set(_QUERY_TOKEN_RE.findall(normalized))
    for token in [token for token in tokens if re.fullmatch(r"[\u3400-\u9fff]+", token)]:
        if len(token) > 1:
            tokens.update(
                token[index : index + 2] for index in range(len(token) - 1)
            )
    return tokens


def _payload_matches_filter(
    payload: dict, key: str, requested: tuple[str, ...] | str
) -> bool:
    actual = payload.get(key)
    actual_values = actual if isinstance(actual, list) else [actual]
    requested_values = (
        requested if isinstance(requested, (list, tuple, set, frozenset)) else [requested]
    )
    requested_scalars = {str(value) for value in requested_values}
    return any(str(value) in requested_scalars for value in actual_values)


class RegexFallbackIndex:
    """In-memory literal matching with no model, network or storage dependency.

    The query is treated as text, never as an untrusted regular expression.
    Filters and trusted allowed IDs are applied before the top-k limit.
    """

    def __init__(self, points: list[VectorPoint]):
        self._points = tuple(points)
        self._prepared_points = tuple(
            (point, self._searchable_text(point)) for point in self._points
        )

    def _eligible(self, point: VectorPoint, filters, allowed_ids) -> bool:
        if allowed_ids is not None and point.id not in allowed_ids:
            return False
        if any(key.startswith("_") for key in filters):
            raise ValueError("internal index fields cannot be caller filters")
        return all(
            _payload_matches_filter(point.payload, key, values)
            for key, values in filters.items()
        )

    @staticmethod
    def _searchable_text(point: VectorPoint) -> str:
        payload = point.payload
        return unicodedata.normalize(
            "NFKC",
            " ".join(
                [
                    str(payload.get("id") or ""),
                    str(payload.get("profile_key") or ""),
                    str(payload.get("worker_id") or ""),
                    str(payload.get("profile_id") or ""),
                    str(payload.get("name") or ""),
                    str(
                        payload.get("content")
                        or payload.get("searchable_text")
                        or payload.get("content_preview")
                        or ""
                    ),
                ]
            ),
        ).casefold()

    def keyword_search(self, query, limit, filters, allowed_ids):
        if not query.strip() or limit <= 0:
            return []
        if allowed_ids == frozenset():
            return []
        tokens = _query_tokens(query)
        if not tokens:
            return []
        matches: list[tuple[float, VectorPoint]] = []
        for point, text in self._prepared_points:
            if not self._eligible(point, filters, allowed_ids):
                continue
            overlap = sum(1 for token in tokens if token in text)
            if overlap:
                score = overlap / len(tokens)
                matches.append((score, point))
        matches.sort(key=lambda item: (-item[0], item[1].id))
        return [
            VectorSearchHit(
                id=point.id,
                score=score,
                payload=dict(point.payload),
            )
            for score, point in matches[:limit]
        ]

    def default_search(self, limit, filters, allowed_ids=None):
        """Return deterministic configured defaults without query overlap.

        This is the terminal availability path used when both semantic and
        lexical retrieval are unavailable. No model, remote service, or
        query-token interpretation is involved; caller filters and trusted
        allowed IDs still decide eligibility.
        """
        if limit is None or limit <= 0:
            return []
        if allowed_ids == frozenset():
            return []
        matches = [
            point for point in self._points
            if self._eligible(point, filters or {}, allowed_ids)
        ]
        matches.sort(key=lambda point: point.id)
        return [
            VectorSearchHit(
                id=point.id,
                score=0.01,
                payload=dict(point.payload),
            )
            for point in matches[:limit]
        ]

    def vector_search(self, vector, limit, filters, allowed_ids):
        # The final path is explicitly lexical; a caller cannot make it depend
        # on an unavailable embedding service by reaching this class directly.
        return self.keyword_search("", limit, filters, allowed_ids)
