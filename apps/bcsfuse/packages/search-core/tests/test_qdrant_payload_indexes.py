"""TDD contract for indexed server-side retrieval filters.

Un-indexed payload filters force Qdrant to evaluate the vector for almost every
point. Server collections must therefore register every bounded v1 filter field
and their internal identity fields as keyword indexes.
"""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client.models import PayloadSchemaType
from qdrant_client import models
from search_core.config.qdrant import QdrantConnection
from search_core.providers.keyword import QdrantKeywordIndex
from search_core.providers.qdrant_payload import (
    DENSE_PAYLOAD_INDEX_FIELDS,
    KEYWORD_PAYLOAD_INDEX_FIELDS,
)
from search_core.providers.qdrant_server import QdrantServerVectorStore


@dataclass
class _Vectors:
    size: int
    distance: str


@dataclass
class _Params:
    vectors: _Vectors


@dataclass
class _Config:
    params: _Params


@dataclass
class _Collection:
    config: _Config


class ProbeClient:
    def __init__(self) -> None:
        self.collection_exists = self._collection_exists
        self.created_collections: list[str] = []
        self.payload_indexes: list[tuple[str, str, PayloadSchemaType]] = []
        self.closed = False

    def _collection_exists(self, collection_name: str) -> bool:
        return False

    def create_collection(self, collection_name: str, **kwargs) -> None:
        self.created_collections.append(collection_name)

    def get_collection(self, collection_name: str) -> _Collection:
        if collection_name not in self.created_collections:
            raise AssertionError("collection was not created first")
        params = _Params(models.VectorParams(size=2, distance="Cosine"))
        params.sparse_vectors = {
            "text": models.SparseVectorParams(modifier=models.Modifier.IDF)
        }
        return _Collection(_Config(params))

    def create_payload_index(
        self,
        collection_name: str,
        field_name: str,
        field_schema: PayloadSchemaType,
    ) -> None:
        self.payload_indexes.append((collection_name, field_name, field_schema))

    def close(self) -> None:
        self.closed = True


def connection() -> QdrantConnection:
    return QdrantConnection(mode="server", url="http://qdrant.example.test:6333")


def test_dense_server_collection_creates_keyword_payload_indexes(monkeypatch):
    client = ProbeClient()
    monkeypatch.setattr(
        "search_core.providers.qdrant_server.remote_client",
        lambda _connection: client,
    )
    store = QdrantServerVectorStore(
        connection=connection(),
        collection_name="dense_contract",
        dimension=2,
    )
    try:
        store._ensure_client()
    finally:
        store.close()

    assert client.created_collections == ["dense_contract"]
    assert client.payload_indexes == [
        ("dense_contract", field, PayloadSchemaType.KEYWORD)
        for field in DENSE_PAYLOAD_INDEX_FIELDS
    ]
    assert DENSE_PAYLOAD_INDEX_FIELDS == (
        "_external_id",
        "availability",
        "runtime_state",
        "domains",
        "trust_level",
        "worker_id",
        "profile_key",
    )


def test_keyword_server_collection_creates_keyword_payload_indexes(monkeypatch):
    client = ProbeClient()
    monkeypatch.setattr(
        "search_core.providers.keyword.remote_client",
        lambda _connection: client,
    )
    index = QdrantKeywordIndex(
        connection=connection(),
        collection_name="keyword_contract",
    )
    try:
        index._ensure_client()
    finally:
        index.close()

    assert client.created_collections == ["keyword_contract"]
    assert client.payload_indexes == [
        ("keyword_contract", field, PayloadSchemaType.KEYWORD)
        for field in KEYWORD_PAYLOAD_INDEX_FIELDS
    ]
    assert KEYWORD_PAYLOAD_INDEX_FIELDS == (
        "_keyword_external_id",
        "_keyword_worker_id",
        "availability",
        "runtime_state",
        "domains",
        "trust_level",
        "worker_id",
        "profile_key",
    )


def test_payload_index_fields_include_every_bounded_v1_filter():
    from search_core.api.filters import SEARCH_FILTER_FIELDS

    supported = frozenset(SEARCH_FILTER_FIELDS)
    assert supported <= set(DENSE_PAYLOAD_INDEX_FIELDS)
    assert supported <= set(KEYWORD_PAYLOAD_INDEX_FIELDS)


def test_composed_server_stores_defer_payload_indexes_until_owner_invocation(monkeypatch):
    dense_client = ProbeClient()
    monkeypatch.setattr(
        "search_core.providers.qdrant_server.remote_client",
        lambda _connection: dense_client,
    )
    dense = QdrantServerVectorStore(
        connection=connection(),
        collection_name="dense_lazy_contract",
        dimension=2,
        payload_indexes_on_connect=False,
    )
    try:
        dense._ensure_client()
        assert dense_client.payload_indexes == []
        dense.ensure_payload_indexes()
        assert dense_client.payload_indexes == [
            ("dense_lazy_contract", field, PayloadSchemaType.KEYWORD)
            for field in DENSE_PAYLOAD_INDEX_FIELDS
        ]
    finally:
        dense.close()

    keyword_client = ProbeClient()
    monkeypatch.setattr(
        "search_core.providers.keyword.remote_client",
        lambda _connection: keyword_client,
    )
    keyword = QdrantKeywordIndex(
        connection=connection(),
        collection_name="keyword_lazy_contract",
        payload_indexes_on_connect=False,
    )
    try:
        keyword._ensure_client()
        assert keyword_client.payload_indexes == []
        keyword.ensure_payload_indexes()
        assert keyword_client.payload_indexes == [
            ("keyword_lazy_contract", field, PayloadSchemaType.KEYWORD)
            for field in KEYWORD_PAYLOAD_INDEX_FIELDS
        ]
    finally:
        keyword.close()
