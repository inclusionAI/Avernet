"""TDD contracts for the process-local final fallback index."""

from __future__ import annotations

import unicodedata

from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.fallback import RegexFallbackIndex


def test_normalizes_payload_text_once_during_index_construction(monkeypatch) -> None:
    point = VectorPoint(
        id="worker:default",
        vector=[],
        payload={"name": "Ｑｕｅｒｙ", "content": "支付 支撐"},
    )
    index = RegexFallbackIndex([point])

    prepared = getattr(index, "_prepared_points", None)
    assert prepared is not None
    assert prepared[0][0] is point
    assert "query" in prepared[0][1]
    assert "支付" in prepared[0][1]

    def reject_searchable_text(point: VectorPoint) -> str:
        raise AssertionError("normalized payload text must not be rebuilt per query")

    monkeypatch.setattr(
        RegexFallbackIndex, "_searchable_text", staticmethod(reject_searchable_text)
    )
    hits = index.keyword_search(
        "query payment", limit=1, filters={}, allowed_ids=None
    )
    assert [hit.id for hit in hits] == ["worker:default"]


def test_precomputed_text_preserves_keyword_and_default_results() -> None:
    point = VectorPoint(
        id="worker:default",
        vector=[],
        payload={
            "worker_id": "worker",
            "profile_key": "worker:default",
            "availability": "public",
            "content": "payment settlement 支付",
        },
    )
    index = RegexFallbackIndex([point])

    hits = index.keyword_search(
        "settlement payment", limit=1, filters={}, allowed_ids=None
    )
    assert [hit.id for hit in hits] == ["worker:default"]
    assert hits[0].score > 0.0

    defaults = index.default_search(limit=1, filters={"availability": "public"})
    assert [hit.id for hit in defaults] == ["worker:default"]
