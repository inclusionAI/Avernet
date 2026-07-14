"""Tests for FaissSqliteVectorStore.

FaissSqliteVectorStore 是 FAISS 内存索引 + SQLite 持久化的组合实现。
用于本地开发环境。
"""

import tempfile
from pathlib import Path

import pytest

from src.domain.models.vector_point import VectorPoint
from src.infra.vectorstores.faiss_sqlite_vector_store import FaissSqliteVectorStore


class TestFaissSqliteVectorStore:
    """Test FaissSqliteVectorStore implementation."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "test_vector.db"

    @pytest.fixture
    def store(self, temp_db):
        """Create a FaissSqliteVectorStore for testing."""
        return FaissSqliteVectorStore(
            dimension=4,
            db_path=str(temp_db),
            auto_load=False,
        )

    @pytest.fixture
    def sample_points(self):
        """Create sample VectorPoints for testing."""
        return [
            VectorPoint(id="point1", vector=[0.1, 0.2, 0.3, 0.4], payload={"name": "First"}),
            VectorPoint(id="point2", vector=[0.5, 0.6, 0.7, 0.8], payload={"name": "Second"}),
            VectorPoint(id="point3", vector=[0.9, 1.0, 0.1, 0.2], payload={"name": "Third"}),
        ]

    def test_initialization(self, store):
        """Test store initializes correctly."""
        assert store.dimension == 4
        assert store.size() == 0

    def test_upsert_and_search(self, store, sample_points):
        """Test upsert and search operations."""
        # Upsert points
        store.upsert(sample_points)
        assert store.size() == 3

        # Search for similar vectors
        hits = store.search([0.1, 0.2, 0.3, 0.4], top_k=2)
        assert len(hits) == 2
        assert hits[0].id == "point1"  # Exact match should be first
        assert hits[0].score > 0.9  # High similarity

    def test_delete(self, store, sample_points):
        """Test delete operation."""
        store.upsert(sample_points)
        assert store.size() == 3

        # Delete one point
        store.delete(["point1"])
        assert store.size() == 2

        # Verify deleted point is not in search results
        hits = store.search([0.1, 0.2, 0.3, 0.4], top_k=3)
        ids = [h.id for h in hits]
        assert "point1" not in ids

    def test_persistence(self, temp_db, sample_points):
        """Test that data persists across store instances."""
        # Create store and add data
        store1 = FaissSqliteVectorStore(
            dimension=4,
            db_path=str(temp_db),
            auto_load=False,
        )
        store1.upsert(sample_points)
        assert store1.size() == 3

        # Create new store instance (simulates restart)
        store2 = FaissSqliteVectorStore(
            dimension=4,
            db_path=str(temp_db),
            auto_load=True,
        )
        assert store2.size() == 3

        # Verify data is accessible
        hits = store2.search([0.1, 0.2, 0.3, 0.4], top_k=2)
        assert len(hits) == 2

    def test_auto_load(self, temp_db, sample_points):
        """Test auto_load functionality."""
        # Create store with auto_load=False, add data
        store1 = FaissSqliteVectorStore(
            dimension=4,
            db_path=str(temp_db),
            auto_load=False,
        )
        store1.upsert(sample_points)

        # Create store with auto_load=True
        store2 = FaissSqliteVectorStore(
            dimension=4,
            db_path=str(temp_db),
            auto_load=True,
        )

        # Verify data was auto-loaded
        assert store2.size() == 3

    def test_sync_from_backend(self, temp_db, sample_points):
        """Test sync_from_backend functionality."""
        # Create two stores sharing the same database
        store1 = FaissSqliteVectorStore(
            dimension=4,
            db_path=str(temp_db),
            auto_load=False,
        )
        store2 = FaissSqliteVectorStore(
            dimension=4,
            db_path=str(temp_db),
            auto_load=False,
        )

        # Add data through store1
        store1.upsert(sample_points)
        assert store1.size() == 3
        assert store2.size() == 0  # store2 doesn't see changes yet

        # Sync store2 from backend
        synced = store2.sync_from_backend(force=True)
        assert synced == 3
        assert store2.size() == 3

    def test_batch_search(self, store, sample_points):
        """Test batch search operation."""
        store.upsert(sample_points)

        vectors = [
            [0.1, 0.2, 0.3, 0.4],
            [0.5, 0.6, 0.7, 0.8],
        ]
        results = store.batch_search(vectors, top_k=2)

        assert len(results) == 2
        assert len(results[0]) == 2
        assert len(results[1]) == 2

    def test_clear(self, store, sample_points):
        """Test clear operation."""
        store.upsert(sample_points)
        assert store.size() == 3

        store.clear()
        assert store.size() == 0

        # Verify backend is also cleared
        assert store._backend.count() == 0

    def test_upsert_updates_existing(self, store):
        """Test that upsert updates existing points."""
        # Insert initial point
        point1 = VectorPoint(id="test", vector=[0.1, 0.2, 0.3, 0.4], payload={"version": 1})
        store.upsert([point1])
        assert store.size() == 1

        # Update with new vector
        point2 = VectorPoint(id="test", vector=[0.5, 0.6, 0.7, 0.8], payload={"version": 2})
        store.upsert([point2])

        # Search should return updated vector
        hits = store.search([0.5, 0.6, 0.7, 0.8], top_k=1)
        assert hits[0].id == "test"
        assert hits[0].payload["version"] == 2


class TestFaissSqliteVectorStoreProtocol:
    """Test that FaissSqliteVectorStore implements VectorStoreAdapter protocol."""

    def test_implements_protocol(self):
        """Verify protocol compliance."""
        from src.domain.services.vector_store_adapter import VectorStoreAdapter

        store = FaissSqliteVectorStore(dimension=4, db_path=":memory:", auto_load=False)

        # Check that store implements the protocol
        assert isinstance(store, VectorStoreAdapter) or _check_protocol_methods(store)


def _check_protocol_methods(store) -> bool:
    """Check that store has all required protocol methods."""
    required_methods = ['upsert', 'delete', 'search', 'batch_search', 'save_snapshot', 'load_snapshot', 'size']
    for method in required_methods:
        if not hasattr(store, method):
            return False
    return True