"""Visibility queries retain vector-provider delegation and legacy fallback."""

import pytest

from src.application.services.worker_vector_match_service import WorkerVectorMatchService
from src.domain.models.metadata_record import MetadataRecord
from src.domain.models.vector_point import VectorPoint
from src.infra.metadatastores.file_metadata_store_adapter import FileMetadataStoreAdapter
from src.infra.public.vectorstores.qdrant_mysql_vector_store import QdrantMySQLVectorStore
from tests.unit.infra.test_qdrant_durable_vector_store import FakePersistenceBackend


@pytest.mark.parametrize("mode", ["legacy", "fragment"])
@pytest.mark.parametrize("filters,expected", [
    ({}, {"bot:1:default", "bot:2:default"}),
    ({"domains": ["absent"]}, set()),
    ({"domains": ["backend"]}, {"bot:1:default"}),
    ({"domains": ["backend", "frontend"]}, {"bot:1:default", "bot:2:default"}),
    ({"domains": ["backend"], "active_skill_names": ["js"]}, set()),
])
def test_payload_filters_are_delegated_even_if_metadata_filter_is_unavailable(
    tmp_path, monkeypatch, mode, filters, expected,
):
    metadata = FileMetadataStoreAdapter(storage_dir=str(tmp_path))
    vectors = QdrantMySQLVectorStore(
        dimension=4, qdrant_path=str(tmp_path / "qdrant"),
        persistence_backend=FakePersistenceBackend(),
    )
    def unavailable(*_args):
        raise RuntimeError("metadata filter unavailable")
    monkeypatch.setattr(metadata, "filter", unavailable)
    for worker, domain, skill in [("bot:1", "backend", "python"), ("bot:2", "frontend", "js")]:
        key = worker + ":default"
        metadata.upsert([MetadataRecord(
            profile_key=key, staff_id=worker, profile_id="default", profile_type="bot",
            source_root="test", domains=[domain], active_skill_names=[skill],
        )])
        vectors.upsert([VectorPoint(id=key, vector=[1.0, 0.0, 0.0, 0.0], payload={
            "profile_key": key, "worker_id": worker, "profile_id": "default",
            "runtime_state": "online", "availability": "public", "fragment_type": "full",
            "domains": [domain], "active_skill_names": [skill],
        })])
    service = WorkerVectorMatchService(vector_store=vectors, metadata_store=metadata)
    try:
        results = service.match(
            query_embedding=[1.0, 0.0, 0.0, 0.0], top_k=10, mode=mode,
            filters=filters, vector_min_score=0.0,
        )
        assert {result.profile_key for result in results} == expected
    finally:
        vectors.close()
