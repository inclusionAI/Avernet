"""FAISS implements the point-read contract used by vector consumers."""

import pytest

from src.domain.models.vector_point import VectorPoint
from src.domain.services.vector_store_adapter import VectorStoreAdapter
from src.infra.vectorstores.faiss_vector_store_adapter import FaissVectorStoreAdapter


def test_get_returns_latest_live_point_and_defensive_payload(tmp_path):
    store = FaissVectorStoreAdapter(dimension=2)
    assert isinstance(store, VectorStoreAdapter)
    assert store.get("missing") is None
    store.upsert([VectorPoint(id="bot:1:default", vector=[3, 4], payload={"tags": ["old"]})])
    point = store.get("bot:1:default")
    assert point.vector == pytest.approx([0.6, 0.8])
    assert point.payload == {"tags": ["old"]}
    point.payload["tags"].append("mutated")
    assert store.get("bot:1:default").payload == {"tags": ["old"]}
    store.upsert([VectorPoint(id="bot:1:default", vector=[0, 1], payload={"tags": ["new"]})])
    assert store.get("bot:1:default").vector == pytest.approx([0, 1])
    store.save_snapshot(str(tmp_path))
    restored = FaissVectorStoreAdapter(dimension=2)
    restored.load_snapshot(str(tmp_path))
    assert restored.get("bot:1:default").payload == {"tags": ["new"]}
    restored.delete(["bot:1:default"])
    assert restored.get("bot:1:default") is None
