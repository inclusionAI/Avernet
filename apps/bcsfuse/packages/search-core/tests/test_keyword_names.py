"""Shared lexical recall: standard name trigrams widen recall."""

from tests.qdrant_support import server_options

import pytest
from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.qdrant_index import QdrantRetrievalIndex


@pytest.fixture
def index(tmp_path):
    store = QdrantRetrievalIndex(
        **server_options(str(tmp_path)), path=str(tmp_path), dimension=2
    )
    yield store
    store.close()


def add(index, key, name, text="", visibility="public"):
    index.upsert(
        [
            VectorPoint(
                id=key,
                vector=[1.0, 0.0],
                payload={
                    "name": name,
                    "search_text": text or name,
                    "visibility": visibility,
                },
            )
        ]
    )


@pytest.mark.parametrize(
    "query,name",
    [
        ("uct", "aliuct值班助手"),
        ("aliuct", "UCT20值班助手"),
        ("grafana", "mygrafana监控"),
        ("elastic", "Elasticsearch助手"),
    ],
)
def test_name_substrings_are_generic_and_do_not_require_aliases(index, query, name):
    add(index, "target", name)
    add(index, "noise", "ap_495831")
    assert [h.id for h in index.keyword_search(query, 10, {})] == ["target"]


def test_name_fragments_do_not_expand_description_terms(index):
    add(index, "exact", "Elastic")
    add(index, "word", "Elastic expert", "Elastic expert")
    add(index, "fragment", "Elasticsearch助手", "Elasticsearch " * 100)
    add(index, "body-only", "通用工具", "Elasticsearch")
    hits = index.keyword_search("elastic", 10, {})
    assert {h.id for h in hits} == {"exact", "word", "fragment"}


def test_fragments_obey_filters_updates_deletes_and_preserve_dense(index):
    add(index, "private", "mygrafana", visibility="private")
    add(index, "public", "grafana助手")
    before = index.vector_search([1.0, 0.0], 10, {})
    assert [
        h.id for h in index.keyword_search("graf", 1, {"visibility": ("public",)})
    ] == ["public"]
    add(index, "public", "天气助手")
    assert not index.keyword_search("graf", 10, {"visibility": ("public",)})
    index.delete(["private"])
    assert not index.keyword_search("graf", 10, {})
    assert index.vector_search([1.0, 0.0], 10, {})[0].score == before[0].score


def test_short_terms_chinese_and_numeric_name_fragments(index):
    add(index, "ascii", "abcdef")
    add(index, "chinese", "游戏助手")
    add(index, "number", "123456")
    assert not index.keyword_search("ab", 10, {})
    assert [h.id for h in index.keyword_search("123", 10, {})] == ["number"]
    assert [h.id for h in index.keyword_search("123456", 10, {})] == ["number"]
    assert [h.id for h in index.keyword_search("游戏", 10, {})] == ["chinese"]
