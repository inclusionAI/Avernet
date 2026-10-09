"""Whole-profile search must not expose fragment IDs as worker/profile IDs."""

import pytest

from src.application.services.worker_vector_match_service import WorkerVectorMatchService
from src.domain.models.metadata_record import MetadataRecord
from src.domain.models.vector_point import VectorPoint
from src.infra.metadatastores.file_metadata_store_adapter import FileMetadataStoreAdapter
from src.infra.vectorstores.faiss_vector_store_adapter import FaissVectorStoreAdapter


@pytest.mark.parametrize("with_metadata", [False, True])
@pytest.mark.parametrize("exclude_first", [False, True])
def test_legacy_search_preserves_canonical_identity(tmp_path, with_metadata, exclude_first):
    vectors = FaissVectorStoreAdapter(dimension=2)
    metadata = FileMetadataStoreAdapter(storage_dir=str(tmp_path))
    for worker, fragments in [("bot:owner", ["full", "profile"]), ("second:owner", ["full"])]:
        key = worker + ":default"
        if with_metadata:
            metadata.upsert([MetadataRecord(
                profile_key=key, staff_id=worker, profile_id="default",
                profile_type="bot", source_root="test", domains=["coding"],
            )])
        for fragment in fragments:
            vectors.upsert([VectorPoint(
                id=key + ":" + fragment, vector=[1, 0],
                payload={"profile_key": key, "worker_id": worker, "profile_id": "default"},
            )])
    service = WorkerVectorMatchService(vector_store=vectors, metadata_store=metadata)
    results = service.match(
        query_embedding=[1, 0], top_k=2, mode="legacy", vector_min_score=0,
        filters={"domains": ["coding"]} if with_metadata else {},
        excluded_profile_keys=["bot:owner:default"] if exclude_first else [],
    )
    expected = {"second:owner:default"} if exclude_first else {"bot:owner:default", "second:owner:default"}
    assert {r.profile_key for r in results} == expected
    assert len(results) == len(expected)
    assert all(r.metadata.profile_id == "default" for r in results)
    assert all(r.metadata.staff_id in {"bot:owner", "second:owner"} for r in results)
