"""Bounded literal fallback used only when both retrieval branches are down."""

from __future__ import annotations

import heapq
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


def _normalised_filters(filters) -> dict[str, frozenset[str]]:
    normalised: dict[str, frozenset[str]] = {}
    for key, values in (filters or {}).items():
        requested_values = (
            values if isinstance(values, (list, tuple, set, frozenset)) else [values]
        )
        normalised[key] = frozenset(str(value) for value in requested_values)
    return normalised


def _payload_matches_filter(
    payload: dict, key: str, requested: tuple[str, ...] | str | frozenset[str]
) -> bool:
    actual = payload.get(key)
    actual_values = actual if isinstance(actual, list) else [actual]
    requested_scalars = (
        requested
        if isinstance(requested, frozenset)
        else {
            str(value)
            for value in (
                requested
                if isinstance(requested, (list, tuple, set, frozenset))
                else [requested]
            )
        }
    )
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
        self._token_index: dict[str, set[int]] = {}
        for position, (_, text) in enumerate(self._prepared_points):
            for token in _query_tokens(text):
                self._token_index.setdefault(token, set()).add(position)

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

    def _positions_for_token(self, token: str) -> frozenset[int]:
        """Return prepared positions matching one query token.

        Exact-key lookup keeps the common path O(number of query tokens).
        A rare substring query ("vector" matching "vectorization") scans the
        bounded vocabulary; it must not force an O(token-length^2) startup
        expansion for every indexed token.
        """

        exact = self._token_index.get(token)
        if exact is not None:
            return frozenset(exact)
        return frozenset(
            position
            for indexed_token, positions in self._token_index.items()
            if token in indexed_token
            for position in positions
        )

    @staticmethod
    def _hits(matches, limit):
        return [
            VectorSearchHit(
                id=point.id,
                score=score,
                payload=dict(point.payload),
            )
            for score, point in matches[:limit]
        ]

    def keyword_search(self, query, limit, filters, allowed_ids):
        if not query.strip() or limit <= 0:
            return []
        if allowed_ids == frozenset():
            return []
        tokens = _query_tokens(query)
        if not tokens:
            return []
        overlaps: dict[int, int] = {}
        for token in tokens:
            for position in self._positions_for_token(token):
                overlaps[position] = overlaps.get(position, 0) + 1
        token_count = len(tokens)
        normalised_filters = _normalised_filters(filters)
        ranked = heapq.nsmallest(
            max(limit * 4, limit),
            overlaps,
            key=lambda position: (
                -overlaps[position],
                self._prepared_points[position][0].id,
            ),
        )
        matches: list[tuple[float, VectorPoint]] = []
        for position in ranked:
            point = self._prepared_points[position][0]
            if not self._eligible(point, normalised_filters, allowed_ids):
                continue
            matches.append((overlaps[position] / token_count, point))
            if len(matches) >= limit:
                return self._hits(matches, limit)
        if len(matches) >= limit:
            return self._hits(matches, limit)
        ranked = sorted(
            overlaps,
            key=lambda position: (
                -overlaps[position],
                self._prepared_points[position][0].id,
            ),
        )
        matches.clear()
        for position in ranked:
            point = self._prepared_points[position][0]
            if not self._eligible(point, normalised_filters, allowed_ids):
                continue
            matches.append((overlaps[position] / token_count, point))
            if len(matches) >= limit:
                break
        return self._hits(matches, limit)

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
        normalised_filters = _normalised_filters(filters or {})
        matches = [
            point for point in self._points
            if self._eligible(point, normalised_filters, allowed_ids)
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
