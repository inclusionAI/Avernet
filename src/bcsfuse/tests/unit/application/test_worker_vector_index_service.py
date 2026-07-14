"""Tests for WorkerVectorIndexService."""

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.domain.models.metadata_record import MetadataRecord
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit
from src.domain.models.worker_profile import WorkerProfile, ProfileType
from src.application.services.worker_vector_index_service import (
    WorkerVectorIndexService,
    IndexResult,
    SearchResult,
)
from src.infra.metadatastores.file_metadata_store_adapter import FileMetadataStoreAdapter
from src.infra.vectorstores.faiss_vector_store_adapter import FaissVectorStoreAdapter


class TestWorkerVectorIndexService:
    """Test WorkerVectorIndexService implementation."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def vector_store(self):
        """Create a FaissVectorStoreAdapter for testing."""
        return FaissVectorStoreAdapter(dimension=4)

    @pytest.fixture
    def metadata_store(self, temp_dir):
        """Create a FileMetadataStoreAdapter for testing."""
        return FileMetadataStoreAdapter(storage_dir=temp_dir)

    @pytest.fixture
    def service(self, vector_store, metadata_store):
        """Create a WorkerVectorIndexService for testing."""
        return WorkerVectorIndexService(
            vector_store=vector_store,
            metadata_store=metadata_store,
        )

    @pytest.fixture
    def sample_profiles(self):
        """Create sample WorkerProfiles for testing."""
        return [
            WorkerProfile(
                staff_id="123",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/data/workers",
                active_skills=[
                    SkillProfile(
                        name="python",
                        description="Python development",
                        skill_id="py_001",
                        skill_set_name="backend",
                    )
                ],
                searchable_text="[SKILL:python:Python development]",
            ),
            WorkerProfile(
                staff_id="456",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/data/workers",
                active_skills=[
                    SkillProfile(
                        name="javascript",
                        description="JavaScript development",
                        skill_id="js_001",
                        skill_set_name="frontend",
                    )
                ],
                searchable_text="[SKILL:javascript:JavaScript development]",
            ),
        ]

    @pytest.fixture
    def sample_embeddings(self):
        """Create sample embeddings for testing (4-dim)."""
        return [
            [0.1, 0.2, 0.3, 0.4],
            [0.5, 0.6, 0.7, 0.8],
        ]

    # ========================================
    # Creation and Initialization Tests
    # ========================================

    def test_create_service(self, vector_store, metadata_store):
        """Test creating WorkerVectorIndexService."""
        service = WorkerVectorIndexService(
            vector_store=vector_store,
            metadata_store=metadata_store,
        )

        assert service.vector_store is vector_store
        assert service.metadata_store is metadata_store

    # ========================================
    # Indexing Tests
    # ========================================

    def test_index_profiles_with_embeddings(
        self, service, sample_profiles, sample_embeddings
    ):
        """Test indexing profiles with embeddings."""
        result = service.index_profiles(sample_profiles, sample_embeddings)

        assert isinstance(result, IndexResult)
        assert result.indexed_count == 2
        assert result.failed_count == 0

        # Verify metadata was stored
        assert service.metadata_store.size() == 2

        # Verify vectors were stored
        assert service.vector_store.size() == 2

    def test_index_profiles_creates_correct_metadata(
        self, service, sample_profiles, sample_embeddings
    ):
        """Test that indexing creates correct metadata records."""
        service.index_profiles(sample_profiles, sample_embeddings)

        record = service.metadata_store.get("staff_123:default")

        assert record is not None
        assert record.staff_id == "123"
        assert record.profile_id == "default"
        assert record.profile_type == "default"
        assert record.source_root == "/data/workers"
        assert "python" in record.active_skill_names

    def test_index_profiles_updates_vector_id_mapping(
        self, service, sample_profiles, sample_embeddings
    ):
        """Test that indexing updates vector_id mapping."""
        service.index_profiles(sample_profiles, sample_embeddings)

        # Get metadata records
        record1 = service.metadata_store.get("staff_123:default")
        record2 = service.metadata_store.get("staff_456:default")

        assert record1 is not None
        assert record1.vector_id is not None
        assert record2 is not None
        assert record2.vector_id is not None

        # Verify vector_ids can be looked up
        results = service.metadata_store.get_by_vector_ids([record1.vector_id])
        assert len(results) == 1

    def test_index_profiles_mismatched_counts_raises_error(
        self, service, sample_profiles
    ):
        """Test that mismatched profile/embedding counts raises error."""
        embeddings = [[0.1, 0.2, 0.3, 0.4]]  # Only one embedding

        with pytest.raises(ValueError, match="count mismatch"):
            service.index_profiles(sample_profiles, embeddings)

    def test_index_profiles_empty_list(self, service):
        """Test indexing empty list."""
        result = service.index_profiles([], [])

        assert result.indexed_count == 0
        assert result.failed_count == 0

    def test_index_profiles_with_domains(
        self, service, sample_embeddings
    ):
        """Test indexing profiles with domain information."""
        profiles = [
            WorkerProfile(
                staff_id="123",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/data/workers",
                active_skills=[
                    SkillProfile(
                        name="python",
                        description="Python development",
                        skill_id="py_001",
                        skill_set_name="backend",
                    )
                ],
                searchable_text="[SKILL:python:Python development]",
            )
        ]

        # Index with domain hints
        domain_hints = [["backend", "devops"]]

        result = service.index_profiles(profiles, sample_embeddings[:1], domain_hints)

        assert result.indexed_count == 1

        record = service.metadata_store.get("staff_123:default")
        assert "backend" in record.domains
        assert "devops" in record.domains

    # ========================================
    # Search Tests
    # ========================================

    def test_search_by_vector(self, service, sample_profiles, sample_embeddings):
        """Test searching by vector."""
        service.index_profiles(sample_profiles, sample_embeddings)

        # Search with first embedding
        query_vector = sample_embeddings[0]
        results = service.search_by_vector(query_vector, top_k=2)

        assert len(results) == 2
        assert isinstance(results[0], SearchResult)
        assert results[0].profile_key == "staff_123:default"
        assert results[0].score > 0.0
        assert results[0].metadata is not None

    def test_search_by_vector_with_metadata_filter(
        self, service, sample_profiles, sample_embeddings
    ):
        """Test searching with metadata filter."""
        service.index_profiles(sample_profiles, sample_embeddings)

        # Filter by skill
        query_vector = [0.1, 0.2, 0.3, 0.4]
        results = service.search_by_vector(
            query_vector,
            top_k=10,
            metadata_filter={"active_skill_names": ["python"]}
        )

        # Should only return profiles with python skill
        for result in results:
            assert "python" in result.metadata.active_skill_names

    def test_search_by_text_with_fake_embedding(
        self, service, sample_profiles, sample_embeddings
    ):
        """Test searching by text with fake embedding function."""
        service.index_profiles(sample_profiles, sample_embeddings)

        # Create fake embedding function
        def fake_embedding_fn(text: str) -> list[float]:
            return [0.1, 0.2, 0.3, 0.4]

        service.set_embedding_function(fake_embedding_fn)

        results = service.search_by_text("python developer", top_k=2)

        assert len(results) <= 2

    # ========================================
    # Filter Tests
    # ========================================

    def test_filter_by_profile_type(self, service, sample_profiles, sample_embeddings):
        """Test filtering by profile_type."""
        service.index_profiles(sample_profiles, sample_embeddings)

        results = service.filter_workers({"profile_type": "default"})

        assert len(results) == 2
        for r in results:
            assert r.profile_type == "default"

    def test_filter_by_skill_names(self, service, sample_profiles, sample_embeddings):
        """Test filtering by skill names."""
        service.index_profiles(sample_profiles, sample_embeddings)

        results = service.filter_workers({"active_skill_names": ["python"]})

        assert len(results) == 1
        assert results[0].staff_id == "123"

    def test_filter_combined(self, service, sample_profiles, sample_embeddings):
        """Test filtering with combined criteria."""
        service.index_profiles(sample_profiles, sample_embeddings)

        results = service.filter_workers({
            "profile_type": "default",
            "active_skill_names": ["javascript"]
        })

        assert len(results) == 1
        assert results[0].staff_id == "456"

    # ========================================
    # Get/Update Tests
    # ========================================

    def test_get_metadata(self, service, sample_profiles, sample_embeddings):
        """Test getting metadata by profile_key."""
        service.index_profiles(sample_profiles, sample_embeddings)

        record = service.get_metadata("staff_123:default")

        assert record is not None
        assert record.staff_id == "123"

    def test_get_metadata_nonexistent(self, service):
        """Test getting nonexistent metadata."""
        record = service.get_metadata("nonexistent")

        assert record is None

    def test_delete_profile(self, service, sample_profiles, sample_embeddings):
        """Test deleting a profile."""
        service.index_profiles(sample_profiles, sample_embeddings)

        service.delete_profiles(["staff_123:default"])

        # Verify metadata deleted
        assert service.metadata_store.get("staff_123:default") is None
        assert service.metadata_store.size() == 1

        # Verify vector deleted
        assert service.vector_store.size() == 1

    # ========================================
    # Persistence Tests
    # ========================================

    def test_save_and_load(self, temp_dir, sample_profiles, sample_embeddings):
        """Test saving and loading index."""
        # Create service and index
        vector_store = FaissVectorStoreAdapter(dimension=4)
        metadata_store = FileMetadataStoreAdapter(storage_dir=temp_dir)
        service = WorkerVectorIndexService(
            vector_store=vector_store,
            metadata_store=metadata_store,
        )
        service.index_profiles(sample_profiles, sample_embeddings)

        # Save
        service.save(temp_dir)

        # Create new service and load
        vector_store2 = FaissVectorStoreAdapter(dimension=4)
        metadata_store2 = FileMetadataStoreAdapter(storage_dir=temp_dir)
        service2 = WorkerVectorIndexService(
            vector_store=vector_store2,
            metadata_store=metadata_store2,
        )
        service2.load(temp_dir)

        # Verify data
        assert service2.metadata_store.size() == 2
        assert service2.vector_store.size() == 2

        # Verify search works
        results = service2.search_by_vector(sample_embeddings[0], top_k=1)
        assert len(results) == 1

    def test_save_creates_all_files(self, service, sample_profiles, sample_embeddings, temp_dir):
        """Test that save creates all expected files."""
        service.index_profiles(sample_profiles, sample_embeddings)
        service.save(temp_dir)

        # Check vector store files
        assert (Path(temp_dir) / "index.faiss").exists()
        assert (Path(temp_dir) / "id_map.json").exists()

        # Check metadata store files
        assert (Path(temp_dir) / "metadata.jsonl").exists()
        assert (Path(temp_dir) / "vector_id_map.json").exists()

    # ========================================
    # Statistics Tests
    # ========================================

    def test_get_stats(self, service, sample_profiles, sample_embeddings):
        """Test getting index statistics."""
        service.index_profiles(sample_profiles, sample_embeddings)

        stats = service.get_stats()

        assert "total_profiles" in stats
        assert stats["total_profiles"] == 2
        assert "total_vectors" in stats
        assert stats["total_vectors"] == 2

    # ========================================
    # Edge Cases
    # ========================================

    def test_index_profile_with_no_skills(self, service):
        """Test indexing profile with no skills."""
        profile = WorkerProfile(
            staff_id="123",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data/workers",
            active_skills=[],
            searchable_text="",
        )
        embedding = [[0.1, 0.2, 0.3, 0.4]]

        result = service.index_profiles([profile], embedding)

        assert result.indexed_count == 1
        record = service.metadata_store.get("staff_123:default")
        assert record.active_skill_names == []

    def test_index_same_profile_twice_updates(self, service):
        """Test that indexing same profile twice updates it."""
        profile = WorkerProfile(
            staff_id="123",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data/workers",
            active_skills=[
                SkillProfile(
                    name="python",
                    description="Python",
                    skill_id="py_001",
                    skill_set_name="backend",
                )
            ],
            searchable_text="[SKILL:python:Python]",
        )

        embedding1 = [[0.1, 0.2, 0.3, 0.4]]
        service.index_profiles([profile], embedding1)

        # Update with new embedding
        embedding2 = [[0.5, 0.6, 0.7, 0.8]]
        service.index_profiles([profile], embedding2)

        # Should still be 1 profile
        assert service.metadata_store.size() == 1
        assert service.vector_store.size() == 1

    def test_search_empty_index(self, service):
        """Test searching empty index."""
        with pytest.raises(ValueError):
            service.search_by_vector([0.1, 0.2, 0.3, 0.4], top_k=1)