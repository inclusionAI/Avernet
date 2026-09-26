"""Integration test for Worker Vector Index flow.

This test validates the complete end-to-end flow with real adapters
but fake embeddings (no LLM dependency).
"""

import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

from src.domain.models.skill_profile import SkillProfile
from src.domain.models.worker_profile import WorkerProfile, ProfileType
from src.application.services.worker_vector_index_service import WorkerVectorIndexService
from src.infra.metadatastores.file_metadata_store_adapter import FileMetadataStoreAdapter
from src.infra.vectorstores.faiss_vector_store_adapter import FaissVectorStoreAdapter


class FakeEmbeddingFunction:
    """Fake embedding function for testing.

    Creates deterministic embeddings based on text content.
    Uses simple hash-based approach for reproducibility.
    """

    def __init__(self, dimension: int = 128):
        self._dimension = dimension

    def __call__(self, text: str) -> list[float]:
        """Generate a deterministic embedding from text."""
        np.random.seed(hash(text) % (2**32))
        vector = np.random.randn(self._dimension).astype(np.float32)
        # Normalize
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        return vector.tolist()


class TestWorkerVectorIndexIntegration:
    """Integration tests for Worker Vector Index flow."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def dimension(self):
        """Vector dimension for testing."""
        return 128

    @pytest.fixture
    def embedding_fn(self, dimension):
        """Create fake embedding function."""
        return FakeEmbeddingFunction(dimension)

    @pytest.fixture
    def vector_store(self, dimension):
        """Create FaissVectorStoreAdapter."""
        return FaissVectorStoreAdapter(dimension=dimension)

    @pytest.fixture
    def metadata_store(self, temp_dir):
        """Create FileMetadataStoreAdapter."""
        return FileMetadataStoreAdapter(storage_dir=temp_dir)

    @pytest.fixture
    def service(self, vector_store, metadata_store, embedding_fn):
        """Create WorkerVectorIndexService with fake embedding."""
        service = WorkerVectorIndexService(
            vector_store=vector_store,
            metadata_store=metadata_store,
        )
        service.set_embedding_function(embedding_fn)
        return service

    @pytest.fixture
    def sample_profiles(self):
        """Create sample worker profiles."""
        return [
            # Backend developer
            WorkerProfile(
                staff_id="001",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/data/workers",
                active_skills=[
                    SkillProfile(
                        name="python",
                        description="Expert Python developer",
                        skill_id="py_001",
                        skill_set_name="backend",
                    ),
                    SkillProfile(
                        name="kubernetes",
                        description="Kubernetes orchestration",
                        skill_id="k8s_001",
                        skill_set_name="devops",
                    ),
                ],
                searchable_text="[SKILL:python:Expert Python developer] [SKILL:kubernetes:Kubernetes orchestration]",
            ),
            # Frontend developer
            WorkerProfile(
                staff_id="002",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/data/workers",
                active_skills=[
                    SkillProfile(
                        name="javascript",
                        description="JavaScript and React",
                        skill_id="js_001",
                        skill_set_name="frontend",
                    ),
                    SkillProfile(
                        name="css",
                        description="CSS styling",
                        skill_id="css_001",
                        skill_set_name="frontend",
                    ),
                ],
                searchable_text="[SKILL:javascript:JavaScript and React] [SKILL:css:CSS styling]",
            ),
            # Full-stack developer
            WorkerProfile(
                staff_id="003",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/data/workers",
                active_skills=[
                    SkillProfile(
                        name="python",
                        description="Python backend",
                        skill_id="py_002",
                        skill_set_name="backend",
                    ),
                    SkillProfile(
                        name="javascript",
                        description="JavaScript frontend",
                        skill_id="js_002",
                        skill_set_name="frontend",
                    ),
                ],
                searchable_text="[SKILL:python:Python backend] [SKILL:javascript:JavaScript frontend]",
            ),
            # Bot profile
            WorkerProfile(
                staff_id="001",
                profile_id="assistant",
                profile_type=ProfileType.BOT,
                source_root="/data/workers",
                active_skills=[
                    SkillProfile(
                        name="general",
                        description="General assistant",
                        skill_id="gen_001",
                        skill_set_name="assistant",
                    ),
                ],
                searchable_text="[SKILL:general:General assistant]",
            ),
        ]

    # ========================================
    # End-to-End Flow Tests
    # ========================================

    def test_complete_indexing_and_search_flow(
        self, service, embedding_fn, sample_profiles, dimension
    ):
        """Test the complete flow: indexing, searching, and filtering."""
        # Generate embeddings
        embeddings = [
            embedding_fn(p.searchable_text)
            for p in sample_profiles
        ]

        # Index profiles
        index_result = service.index_profiles(
            profiles=sample_profiles,
            embeddings=embeddings,
            domain_hints=[
                ["backend", "devops"],
                ["frontend"],
                ["backend", "frontend"],
                [],
            ],
        )

        assert index_result.indexed_count == 4
        assert index_result.failed_count == 0

        # Verify metadata
        assert service.metadata_store.size() == 4

        # Test 1: Filter by skill
        python_devs = service.filter_workers({"active_skill_names": ["python"]})
        assert len(python_devs) == 2  # staff_001 and staff_003

        # Test 2: Filter by profile type
        bots = service.filter_workers({"profile_type": "bot"})
        assert len(bots) == 1
        assert bots[0].staff_id == "001"
        assert bots[0].profile_id == "assistant"

        # Test 3: Search by vector
        query_vector = embedding_fn("python developer")
        results = service.search_by_vector(query_vector, top_k=3)

        assert len(results) == 3
        # Should find python developers
        found_staff_ids = {r.metadata.staff_id for r in results}
        assert "001" in found_staff_ids or "003" in found_staff_ids

        # Test 4: Search with metadata filter
        results = service.search_by_vector(
            query_vector,
            top_k=10,
            metadata_filter={"profile_type": "default"}
        )

        assert len(results) <= 3  # Only default profiles
        for r in results:
            assert r.metadata.profile_type == "default"

        # Test 5: Combined filter
        results = service.filter_workers({
            "domains": ["backend"],
            "active_skill_names": ["python"],
        })
        assert len(results) == 2  # staff_001 and staff_003

    def test_save_and_load_persistence(
        self, temp_dir, embedding_fn, sample_profiles, dimension
    ):
        """Test that data persists correctly after save/load."""
        # Create service and index
        vector_store = FaissVectorStoreAdapter(dimension=dimension)
        metadata_store = FileMetadataStoreAdapter(storage_dir=temp_dir)
        service = WorkerVectorIndexService(
            vector_store=vector_store,
            metadata_store=metadata_store,
        )

        embeddings = [embedding_fn(p.searchable_text) for p in sample_profiles]
        service.index_profiles(sample_profiles, embeddings)

        # Save
        service.save(temp_dir)

        # Create new service and load
        vector_store2 = FaissVectorStoreAdapter(dimension=dimension)
        metadata_store2 = FileMetadataStoreAdapter(storage_dir=temp_dir)
        service2 = WorkerVectorIndexService(
            vector_store=vector_store2,
            metadata_store=metadata_store2,
        )
        service2.load(temp_dir)

        # Verify all data
        assert service2.metadata_store.size() == 4
        assert service2.vector_store.size() == 4

        # Verify search works
        query_vector = embedding_fn("javascript developer")
        results = service2.search_by_vector(query_vector, top_k=2)

        assert len(results) == 2

        # Verify metadata intact
        record = service2.get_metadata("staff_001:default")
        assert record is not None
        assert record.vector_id is not None
        assert "python" in record.active_skill_names

    def test_update_existing_profile(
        self, service, embedding_fn, sample_profiles, dimension
    ):
        """Test that updating an existing profile works correctly."""
        # Initial index
        embeddings = [embedding_fn(p.searchable_text) for p in sample_profiles[:2]]
        service.index_profiles(sample_profiles[:2], embeddings)

        assert service.metadata_store.size() == 2

        # Get initial record
        initial_record = service.get_metadata("staff_001:default")
        initial_vector_id = initial_record.vector_id

        # Update profile with new embedding
        new_embedding = embedding_fn("updated python expert")
        updated_profile = WorkerProfile(
            staff_id="001",
            profile_id="default",
            profile_type=ProfileType.DEFAULT,
            source_root="/data/workers",
            active_skills=[
                SkillProfile(
                    name="python",
                    description="Updated Python expert",
                    skill_id="py_001",
                    skill_set_name="backend",
                ),
                SkillProfile(
                    name="golang",
                    description="New Golang skill",
                    skill_id="go_001",
                    skill_set_name="backend",
                ),
            ],
            searchable_text="[SKILL:python:Updated Python expert] [SKILL:golang:New Golang skill]",
        )

        service.index_profiles([updated_profile], [new_embedding])

        # Should still be 2 profiles
        assert service.metadata_store.size() == 2

        # Verify updated metadata
        updated_record = service.get_metadata("staff_001:default")
        assert "golang" in updated_record.active_skill_names

    def test_delete_and_search(
        self, service, embedding_fn, sample_profiles
    ):
        """Test that deleted profiles don't appear in search results."""
        embeddings = [embedding_fn(p.searchable_text) for p in sample_profiles]
        service.index_profiles(sample_profiles, embeddings)

        # Delete one profile
        service.delete_profiles(["staff_001:default"])

        # Verify deleted
        assert service.metadata_store.size() == 3
        assert service.vector_store.size() == 3

        # Search should not return deleted profile
        query_vector = embedding_fn("python developer")
        results = service.search_by_vector(query_vector, top_k=10)

        for r in results:
            assert r.profile_key != "staff_001:default"

    def test_filter_by_domains(
        self, service, embedding_fn, sample_profiles
    ):
        """Test filtering by domains."""
        embeddings = [embedding_fn(p.searchable_text) for p in sample_profiles]
        domain_hints = [
            ["backend", "devops"],
            ["frontend"],
            ["backend", "frontend"],
            [],
        ]
        service.index_profiles(sample_profiles, embeddings, domain_hints)

        # Filter by backend domain
        backend_devs = service.filter_workers({"domains": ["backend"]})
        assert len(backend_devs) == 2  # staff_001 and staff_003

        # Filter by both backend and frontend (OR)
        full_stack = service.filter_workers({"domains": ["backend", "frontend"]})
        # Should match profiles with backend OR frontend
        # staff_001 has backend, staff_002 has frontend, staff_003 has both
        assert len(full_stack) == 3

    def test_empty_filter_returns_all(
        self, service, embedding_fn, sample_profiles
    ):
        """Test that empty filter returns all profiles."""
        embeddings = [embedding_fn(p.searchable_text) for p in sample_profiles]
        service.index_profiles(sample_profiles, embeddings)

        results = service.filter_workers()
        assert len(results) == 4

    def test_search_by_text_with_embedding_fn(
        self, service, embedding_fn, sample_profiles
    ):
        """Test text search using embedding function."""
        embeddings = [embedding_fn(p.searchable_text) for p in sample_profiles]
        service.index_profiles(sample_profiles, embeddings)

        # Search by text
        results = service.search_by_text("python backend developer", top_k=3)

        assert len(results) == 3
        # Should find python developers
        found_skills = set()
        for r in results:
            found_skills.update(r.metadata.active_skill_names)
        assert "python" in found_skills

    # ========================================
    # Edge Cases
    # ========================================

    def test_index_large_batch(self, service, embedding_fn, dimension):
        """Test indexing a large batch of profiles."""
        # Create many profiles
        profiles = []
        for i in range(50):
            profiles.append(WorkerProfile(
                staff_id=f"{i:03d}",
                profile_id="default",
                profile_type=ProfileType.DEFAULT,
                source_root="/data/workers",
                active_skills=[
                    SkillProfile(
                        name=f"skill_{i % 5}",
                        description=f"Skill {i % 5}",
                        skill_id=f"skill_{i % 5}",
                        skill_set_name="general",
                    )
                ],
                searchable_text=f"[SKILL:skill_{i % 5}:Skill {i % 5}]",
            ))

        embeddings = [embedding_fn(p.searchable_text) for p in profiles]
        result = service.index_profiles(profiles, embeddings)

        assert result.indexed_count == 50

        # Search should work
        query_vector = embedding_fn("skill 0")
        results = service.search_by_vector(query_vector, top_k=10)
        assert len(results) <= 10

    def test_multiple_profiles_same_staff(
        self, service, embedding_fn, sample_profiles
    ):
        """Test that a staff can have multiple profiles (default + bot)."""
        embeddings = [embedding_fn(p.searchable_text) for p in sample_profiles]
        service.index_profiles(sample_profiles, embeddings)

        # Staff 001 should have two profiles
        all_profiles = service.filter_workers()
        staff_001_profiles = [p for p in all_profiles if p.staff_id == "001"]

        assert len(staff_001_profiles) == 2
        profile_ids = {p.profile_id for p in staff_001_profiles}
        assert "default" in profile_ids
        assert "assistant" in profile_ids

    def test_get_stats(self, service, embedding_fn, sample_profiles):
        """Test getting index statistics."""
        embeddings = [embedding_fn(p.searchable_text) for p in sample_profiles]
        service.index_profiles(sample_profiles, embeddings)

        stats = service.get_stats()

        assert stats["total_profiles"] == 4
        assert stats["total_vectors"] == 4