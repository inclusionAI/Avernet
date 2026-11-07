"""Keyword recall must survive filters, lifecycle changes and rerank failures."""

import math

import pytest
from app.domain.models.vector_point import VectorPoint
from app.domain.services.vector_persistence_backend import VectorChangeSet
from app.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)

from tests.unit.infra.test_qdrant_durable_vector_store import FakePersistenceBackend


def point(worker, content, *, kind="full", similarity=0.1, **payload):
    return VectorPoint(
        id=f"{worker}:default:{kind}",
        vector=[similarity, math.sqrt(1 - similarity**2)],
        payload={
            "worker_id": worker,
            "profile_id": "default",
            "profile_key": f"{worker}:default",
            "fragment_type": kind,
            "runtime_state": "online",
            "availability": "public",
            "content": content,
            **payload,
        },
    )


@pytest.fixture
def store(tmp_path):
    instance = QdrantMySQLVectorStore(
        dimension=2,
        qdrant_path=str(tmp_path / "vectors"),
        persistence_backend=FakePersistenceBackend(),
    )
    yield instance
    instance.close()


def test_chinese_name_and_numeric_id_work_for_historical_payload(store):
    store.upsert([point("bot:12345", "名称: 星河测试3\n身份: 数据工程助手")])
    hits = store.text_search("12345星河:", 10)
    assert [h.id for h in hits] == ["bot:12345:default:full"]
    assert hits[0].score > 0
    assert store.text_search("星河", 10)
    assert store.text_search("1234", 10) == []  # numeric IDs are whole tokens


def test_filters_apply_before_top_k_and_exact_id_does_not_bypass_them(store):
    store.upsert(
        [
            point(f"secret{i}:12345", "星河 " * 20, availability="private")
            for i in range(30)
        ]
        + [
            point("offline:12345", "星河", runtime_state="offline"),
            point("allowed:12345", "星河", availability="protected"),
        ]
    )
    filters = {"availability": ["public", "protected"], "runtime_state": ["online"]}
    assert [
        h.payload["worker_id"] for h in store.text_search("12345星河", 1, filters)
    ] == ["allowed:12345"]
    assert all(
        h.payload["availability"] != "private"
        for h in store.text_search("secret0:12345", 1, filters)
    )






def test_exact_worker_id_first_and_preview_only_records_searchable(store):
    store.upsert(
        [
            point("bot:12345", "", content_preview="星河助手"),
            point("other", "bot 12345 " * 10),
        ]
    )
    assert store.text_search("bot:12345", 1)[0].payload["worker_id"] == "bot:12345"
    assert store.text_search("星河", 1)[0].id == "bot:12345:default:full"
    assert store.text_search("   ", 10) == []










def test_keyword_index_tracks_rebuild_update_delete_and_tombstones(store):
    backend = store._persistence
    backend.save(point("bot", "旧名字"))
    store.rebuild_from_backend()
    assert store.text_search("旧名字", 10)
    store.upsert([point("bot", "新名称")])
    assert store.text_search("旧名字", 10) == []
    assert store.text_search("新名称", 10)
    backend.changes = VectorChangeSet(deleted_ids=["bot:default:full"], checkpoint=1)
    store.sync_incremental()
    assert store.text_search("新名称", 10) == []
    backend.changes = VectorChangeSet(checkpoint=2)
    store.rebuild_from_backend()
    assert store.text_search("新名称", 10)
    store.update_payload_by_worker("bot", {"runtime_state": "offline"})
    assert store.text_search("新名称", 10, {"runtime_state": ["online"]}) == []
    store.delete_by_worker("bot")
    assert store.text_search("新名称", 10) == []


def test_keyword_write_failure_requires_rebuild_and_preserves_durable_payload(
    store, monkeypatch
):
    def fail(*args, **kwargs):
        raise RuntimeError("keyword index unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(store._keywords, "upsert", fail)
        with pytest.raises(RuntimeError, match="REBUILD_REQUIRED"):
            store.upsert([point("bot", "星河助手")])
    with pytest.raises(RuntimeError, match="requires rebuild"):
        store.text_search("星河", 10)
    persisted = store._persistence.points["bot:default:full"]
    assert not any(key.startswith("_keyword") for key in persisted.payload)
    store.rebuild_from_backend()
    assert [hit.id for hit in store.text_search("星河", 10)] == [persisted.id]






















@pytest.mark.parametrize(
    "response",
    [
        {},
        {"scores": []},
        {"scores": [float("nan")]},
        {"results": [{"index": 2, "relevance_score": 0.9}]},
        {"scores": [0.1, 0.2]},
    ],
)
def test_http_model_rejects_invalid_results_instead_of_fabricating_scores(response):
    from app.infra.reranker.http_reranker import HttpReranker

    with pytest.raises(ValueError):
        HttpReranker()._parse_response(response, 1)
