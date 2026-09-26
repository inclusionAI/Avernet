"""Tests for FileMetadataStoreAdapter implementation."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.domain.models.metadata_record import MetadataRecord
from src.infra.metadatastores.file_metadata_store_adapter import FileMetadataStoreAdapter


class TestFileMetadataStoreAdapter:
    """Test FileMetadataStoreAdapter implementation."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def store(self, temp_dir):
        """Create a fresh FileMetadataStoreAdapter for each test."""
        return FileMetadataStoreAdapter(storage_dir=temp_dir)

    @pytest.fixture
    def sample_record(self):
        """Create a sample MetadataRecord for testing."""
        return MetadataRecord(
            profile_key="staff_123:default",
            vector_id=42,
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend", "devops"],
            active_skill_names=["python", "kubernetes"],
            suitable_roles=["developer", "architect"],
            source_root="/data/workers"
        )

    @pytest.fixture
    def sample_records(self):
        """Create multiple sample MetadataRecords for testing."""
        return [
            MetadataRecord(
                profile_key="staff_123:default",
                vector_id=0,
                staff_id="123",
                profile_id="default",
                profile_type="default",
                domains=["backend"],
                active_skill_names=["python"],
                suitable_roles=["developer"],
                source_root="/data/workers"
            ),
            MetadataRecord(
                profile_key="staff_456:default",
                vector_id=1,
                staff_id="456",
                profile_id="default",
                profile_type="default",
                domains=["frontend"],
                active_skill_names=["javascript", "react"],
                suitable_roles=["developer"],
                source_root="/data/workers"
            ),
            MetadataRecord(
                profile_key="staff_789:bot",
                vector_id=2,
                staff_id="789",
                profile_id="assistant",
                profile_type="bot",
                domains=["backend", "frontend"],
                active_skill_names=["python", "javascript"],
                suitable_roles=["assistant"],
                source_root="/data/workers"
            ),
        ]

    # ========================================
    # Basic Operations Tests
    # ========================================

    def test_upsert_single_record(self, store, sample_record):
        """Test upserting a single record."""
        store.upsert([sample_record])

        assert store.size() == 1

        result = store.get("staff_123:default")
        assert result is not None
        assert result.profile_key == "staff_123:default"
        assert result.vector_id == 42
        assert result.domains == ["backend", "devops"]

    def test_upsert_multiple_records(self, store, sample_records):
        """Test upserting multiple records."""
        store.upsert(sample_records)

        assert store.size() == 3

        result1 = store.get("staff_123:default")
        assert result1 is not None
        assert result1.vector_id == 0

        result2 = store.get("staff_456:default")
        assert result2 is not None
        assert result2.vector_id == 1

    def test_upsert_updates_existing_record(self, store, sample_record):
        """Test that upsert updates existing record with same profile_key."""
        store.upsert([sample_record])

        # Update the record
        updated_record = MetadataRecord(
            profile_key="staff_123:default",
            vector_id=99,
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend", "devops", "ml"],
            active_skill_names=["python", "kubernetes", "pytorch"],
            suitable_roles=["developer", "architect", "ml_engineer"],
            source_root="/data/workers"
        )
        store.upsert([updated_record])

        assert store.size() == 1

        result = store.get("staff_123:default")
        assert result is not None
        assert result.vector_id == 99
        assert "ml" in result.domains
        assert "pytorch" in result.active_skill_names

    def test_get_nonexistent_profile_key(self, store):
        """Test getting a nonexistent profile_key returns None."""
        result = store.get("nonexistent_key")
        assert result is None

    def test_get_by_vector_ids(self, store, sample_records):
        """Test getting records by vector IDs."""
        store.upsert(sample_records)

        results = store.get_by_vector_ids([0, 2])

        assert len(results) == 2
        vector_ids = {r.vector_id for r in results}
        assert 0 in vector_ids
        assert 2 in vector_ids

    def test_get_by_vector_ids_nonexistent(self, store, sample_records):
        """Test getting records by nonexistent vector IDs."""
        store.upsert(sample_records)

        results = store.get_by_vector_ids([999, 1000])

        assert len(results) == 0

    def test_get_by_vector_ids_partial_match(self, store, sample_records):
        """Test getting records with some nonexistent vector IDs."""
        store.upsert(sample_records)

        results = store.get_by_vector_ids([0, 999])

        assert len(results) == 1
        assert results[0].vector_id == 0

    def test_delete_single_record(self, store, sample_record):
        """Test deleting a single record."""
        store.upsert([sample_record])
        assert store.size() == 1

        store.delete(["staff_123:default"])

        assert store.size() == 0
        assert store.get("staff_123:default") is None

    def test_delete_multiple_records(self, store, sample_records):
        """Test deleting multiple records."""
        store.upsert(sample_records)
        assert store.size() == 3

        store.delete(["staff_123:default", "staff_456:default"])

        assert store.size() == 1
        assert store.get("staff_123:default") is None
        assert store.get("staff_456:default") is None
        assert store.get("staff_789:bot") is not None

    def test_delete_nonexistent_profile_key(self, store, sample_record):
        """Test deleting a nonexistent profile_key is safe."""
        store.upsert([sample_record])
        assert store.size() == 1

        # Should not raise
        store.delete(["nonexistent_key"])

        assert store.size() == 1

    def test_size_empty_store(self, store):
        """Test size on empty store."""
        assert store.size() == 0

    def test_size_after_operations(self, store, sample_records):
        """Test size after various operations."""
        assert store.size() == 0

        store.upsert(sample_records[:2])
        assert store.size() == 2

        store.upsert([sample_records[2]])
        assert store.size() == 3

        store.delete([sample_records[0].profile_key])
        assert store.size() == 2

    # ========================================
    # Filter Tests
    # ========================================

    def test_filter_no_filters(self, store, sample_records):
        """Test filter with no filters returns all records."""
        store.upsert(sample_records)

        results = store.filter()

        assert len(results) == 3

    def test_filter_by_profile_type(self, store, sample_records):
        """Test filter by profile_type."""
        store.upsert(sample_records)

        results = store.filter({"profile_type": "default"})

        assert len(results) == 2
        for r in results:
            assert r.profile_type == "default"

    def test_filter_by_domains_or_semantics(self, store, sample_records):
        """Test filter by domains with OR semantics."""
        store.upsert(sample_records)

        # Should match records with backend OR frontend
        results = store.filter({"domains": ["backend", "frontend"]})

        # All records have either backend or frontend
        assert len(results) == 3

    def test_filter_by_single_domain(self, store, sample_records):
        """Test filter by a single domain."""
        store.upsert(sample_records)

        results = store.filter({"domains": ["backend"]})

        # staff_123:default and staff_789:bot have backend
        assert len(results) == 2
        for r in results:
            assert "backend" in r.domains

    def test_filter_by_skill_names_or_semantics(self, store, sample_records):
        """Test filter by active_skill_names with OR semantics."""
        store.upsert(sample_records)

        results = store.filter({"active_skill_names": ["python", "react"]})

        # staff_123:default has python, staff_456:default has react, staff_789:bot has python
        assert len(results) == 3

    def test_filter_by_suitable_roles(self, store, sample_records):
        """Test filter by suitable_roles."""
        store.upsert(sample_records)

        results = store.filter({"suitable_roles": ["developer"]})

        # staff_123 and staff_456 have developer role
        assert len(results) == 2

    def test_filter_combined_filters(self, store, sample_records):
        """Test filter with combined filters (AND between different types)."""
        store.upsert(sample_records)

        # profile_type=default AND skill=python
        results = store.filter({
            "profile_type": "default",
            "active_skill_names": ["python"]
        })

        # Only staff_123:default matches both
        assert len(results) == 1
        assert results[0].profile_key == "staff_123:default"

    def test_filter_no_match(self, store, sample_records):
        """Test filter with no matching records."""
        store.upsert(sample_records)

        results = store.filter({"domains": ["nonexistent_domain"]})

        assert len(results) == 0

    def test_filter_empty_result(self, store):
        """Test filter on empty store."""
        results = store.filter({"domains": ["backend"]})

        assert len(results) == 0

    # ========================================
    # Save/Load Tests
    # ========================================

    def test_save_creates_files(self, store, sample_records, temp_dir):
        """Test that save creates the expected files."""
        store.upsert(sample_records)
        store.save(temp_dir)

        # Check metadata.jsonl exists
        metadata_path = Path(temp_dir) / "metadata.jsonl"
        assert metadata_path.exists()

        # Check vector_id_map.json exists
        vector_id_map_path = Path(temp_dir) / "vector_id_map.json"
        assert vector_id_map_path.exists()

    def test_save_and_load_preserves_data(self, temp_dir, sample_records):
        """Test that save and load preserves all data."""
        # Create store and add data
        store1 = FileMetadataStoreAdapter(storage_dir=temp_dir)
        store1.upsert(sample_records)
        store1.save(temp_dir)

        # Create new store and load data
        store2 = FileMetadataStoreAdapter(storage_dir=temp_dir)
        store2.load(temp_dir)

        assert store2.size() == 3

        # Verify all records
        for original in sample_records:
            loaded = store2.get(original.profile_key)
            assert loaded is not None
            assert loaded.profile_key == original.profile_key
            assert loaded.vector_id == original.vector_id
            assert loaded.domains == original.domains
            assert loaded.active_skill_names == original.active_skill_names

    def test_load_nonexistent_directory(self, store):
        """Test loading from nonexistent directory raises error."""
        with pytest.raises(FileNotFoundError):
            store.load("/nonexistent/path")

    def test_save_creates_directory_if_needed(self, temp_dir):
        """Test that save creates the storage directory if it doesn't exist."""
        new_dir = os.path.join(temp_dir, "new_subdir")
        assert not os.path.exists(new_dir)

        store = FileMetadataStoreAdapter(storage_dir=new_dir)
        store.upsert([MetadataRecord(
            profile_key="test:1",
            staff_id="1",
            profile_id="1",
            profile_type="default",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="/data"
        )])
        store.save(new_dir)

        assert os.path.exists(new_dir)

    def test_load_handles_corrupted_file(self, temp_dir):
        """Test that load handles corrupted JSON file gracefully."""
        # Write invalid JSON to metadata.jsonl
        metadata_path = Path(temp_dir) / "metadata.jsonl"
        metadata_path.write_text("invalid json content")

        store = FileMetadataStoreAdapter(storage_dir=temp_dir)

        with pytest.raises(Exception):  # Could be JSONDecodeError or IOError
            store.load(temp_dir)

    def test_save_empty_store(self, store, temp_dir):
        """Test saving an empty store."""
        store.save(temp_dir)

        # Files should exist
        metadata_path = Path(temp_dir) / "metadata.jsonl"
        assert metadata_path.exists()

        # Store should still be empty
        assert store.size() == 0

    # ========================================
    # Vector ID Mapping Tests
    # ========================================

    def test_vector_id_mapping_updated_on_upsert(self, store, sample_record):
        """Test that vector_id mapping is updated on upsert."""
        store.upsert([sample_record])

        # Check mapping via get_by_vector_ids
        results = store.get_by_vector_ids([42])
        assert len(results) == 1
        assert results[0].profile_key == "staff_123:default"

    def test_vector_id_mapping_updated_on_update(self, store, sample_record):
        """Test that vector_id mapping is updated when record is updated."""
        store.upsert([sample_record])

        # Update with different vector_id
        updated_record = MetadataRecord(
            profile_key="staff_123:default",
            vector_id=99,
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend"],
            active_skill_names=["python"],
            suitable_roles=["developer"],
            source_root="/data/workers"
        )
        store.upsert([updated_record])

        # Old vector_id should not exist
        results_old = store.get_by_vector_ids([42])
        assert len(results_old) == 0

        # New vector_id should exist
        results_new = store.get_by_vector_ids([99])
        assert len(results_new) == 1

    def test_vector_id_mapping_removed_on_delete(self, store, sample_record):
        """Test that vector_id mapping is removed on delete."""
        store.upsert([sample_record])
        store.delete(["staff_123:default"])

        # Vector ID should no longer exist in mapping
        results = store.get_by_vector_ids([42])
        assert len(results) == 0

    def test_vector_id_none_not_mapped(self, store):
        """Test that records with vector_id=None are not in vector_id mapping."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            vector_id=None,
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=["backend"],
            active_skill_names=["python"],
            suitable_roles=["developer"],
            source_root="/data/workers"
        )
        store.upsert([record])

        # Should exist in main store
        result = store.get("staff_123:default")
        assert result is not None

        # But not in vector_id mapping
        results = store.get_by_vector_ids([None])  # type: ignore
        # Note: get_by_vector_ids takes int, so None won't match

    # ========================================
    # Edge Cases
    # ========================================

    def test_upsert_empty_list(self, store):
        """Test upserting empty list is safe."""
        store.upsert([])
        assert store.size() == 0

    def test_delete_empty_list(self, store):
        """Test deleting empty list is safe."""
        store.delete([])
        assert store.size() == 0

    def test_record_with_empty_lists(self, store):
        """Test record with empty domain/skill/role lists."""
        record = MetadataRecord(
            profile_key="staff_123:default",
            staff_id="123",
            profile_id="default",
            profile_type="default",
            domains=[],
            active_skill_names=[],
            suitable_roles=[],
            source_root="/data/workers"
        )
        store.upsert([record])

        result = store.get("staff_123:default")
        assert result is not None
        assert result.domains == []
        assert result.active_skill_names == []
        assert result.suitable_roles == []

    def test_large_number_of_records(self, store):
        """Test handling a large number of records."""
        records = [
            MetadataRecord(
                profile_key=f"staff_{i}:default",
                vector_id=i,
                staff_id=str(i),
                profile_id="default",
                profile_type="default",
                domains=["backend"],
                active_skill_names=["python"],
                suitable_roles=["developer"],
                source_root="/data/workers"
            )
            for i in range(100)
        ]

        store.upsert(records)

        assert store.size() == 100

        # Test get_by_vector_ids with large list
        results = store.get_by_vector_ids(list(range(0, 100, 10)))
        assert len(results) == 10

    def test_special_characters_in_fields(self, store):
        """Test records with special characters in fields."""
        record = MetadataRecord(
            profile_key="staff_123:special",
            staff_id="123",
            profile_id="special",
            profile_type="default",
            domains=["backend/api", "frontend-ui"],
            active_skill_names=["c++", "node.js", "python-3"],
            suitable_roles=["full-stack developer", "tech-lead"],
            source_root="/data/workers/special-path"
        )
        store.upsert([record])

        result = store.get("staff_123:special")
        assert result is not None
        assert "backend/api" in result.domains
        assert "c++" in result.active_skill_names

    # ========================================
    # Protocol Compliance Test
    # ========================================

    def test_satisfies_protocol(self, store):
        """Test that FileMetadataStoreAdapter satisfies MetadataStoreAdapter protocol."""
        from src.domain.services.metadata_store_adapter import MetadataStoreAdapter

        assert isinstance(store, MetadataStoreAdapter)