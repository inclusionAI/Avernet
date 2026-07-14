"""Tests for MetadataStoreAdapter protocol."""

import pytest

from src.domain.services.metadata_store_adapter import MetadataStoreAdapter
from src.domain.models.metadata_record import MetadataRecord


class MockMetadataStore:
    """Mock implementation of MetadataStoreAdapter for testing."""

    def __init__(self):
        self._records: dict[str, MetadataRecord] = {}
        self._vector_id_map: dict[int, str] = {}

    def upsert(self, records: list[MetadataRecord]) -> None:
        for record in records:
            self._records[record.profile_key] = record
            if record.vector_id is not None:
                self._vector_id_map[record.vector_id] = record.profile_key

    def get(self, profile_key: str) -> MetadataRecord | None:
        return self._records.get(profile_key)

    def get_by_vector_ids(self, vector_ids: list[int]) -> list[MetadataRecord]:
        results = []
        for vid in vector_ids:
            if vid in self._vector_id_map:
                profile_key = self._vector_id_map[vid]
                record = self._records.get(profile_key)
                if record:
                    results.append(record)
        return results

    def filter(self, filters: dict | None = None) -> list[MetadataRecord]:
        if not filters:
            return list(self._records.values())

        results = []
        for record in self._records.values():
            match = True

            # Filter by domains (OR semantics)
            if "domains" in filters:
                if not any(d in record.domains for d in filters["domains"]):
                    match = False

            # Filter by profile_type
            if "profile_type" in filters:
                if record.profile_type != filters["profile_type"]:
                    match = False

            # Filter by active_skill_names (OR semantics)
            if "active_skill_names" in filters:
                if not any(s in record.active_skill_names for s in filters["active_skill_names"]):
                    match = False

            # Filter by suitable_roles (OR semantics)
            if "suitable_roles" in filters:
                if not any(r in record.suitable_roles for r in filters["suitable_roles"]):
                    match = False

            if match:
                results.append(record)

        return results

    def delete(self, profile_keys: list[str]) -> None:
        for key in profile_keys:
            record = self._records.pop(key, None)
            if record and record.vector_id is not None:
                self._vector_id_map.pop(record.vector_id, None)

    def save(self, path: str) -> None:
        pass

    def load(self, path: str) -> None:
        pass

    def size(self) -> int:
        return len(self._records)


class TestMetadataStoreAdapterProtocol:
    """Test MetadataStoreAdapter protocol."""

    def test_protocol_is_runtime_checkable(self):
        """Test that MetadataStoreAdapter is runtime checkable."""
        mock_store = MockMetadataStore()

        # Should pass isinstance check
        assert isinstance(mock_store, MetadataStoreAdapter)

    def test_protocol_methods_exist(self):
        """Test that all required methods exist in implementation."""
        mock_store = MockMetadataStore()

        # Check all methods exist
        assert hasattr(mock_store, "upsert")
        assert hasattr(mock_store, "get")
        assert hasattr(mock_store, "get_by_vector_ids")
        assert hasattr(mock_store, "filter")
        assert hasattr(mock_store, "delete")
        assert hasattr(mock_store, "save")
        assert hasattr(mock_store, "load")
        assert hasattr(mock_store, "size")

    def test_protocol_upsert_method(self):
        """Test upsert method signature."""
        mock_store = MockMetadataStore()

        records = [
            MetadataRecord(
                profile_key="staff_123:default",
                staff_id="123",
                profile_id="default",
                profile_type="default",
                domains=["backend"],
                active_skill_names=["python"],
                suitable_roles=["developer"],
                source_root="/data"
            )
        ]

        # Should not raise
        mock_store.upsert(records)

        assert mock_store.size() == 1

    def test_protocol_get_method(self):
        """Test get method signature."""
        mock_store = MockMetadataStore()

        records = [
            MetadataRecord(
                profile_key="staff_123:default",
                staff_id="123",
                profile_id="default",
                profile_type="default",
                domains=["backend"],
                active_skill_names=["python"],
                suitable_roles=["developer"],
                source_root="/data"
            )
        ]
        mock_store.upsert(records)

        result = mock_store.get("staff_123:default")

        assert result is not None
        assert isinstance(result, MetadataRecord)
        assert result.profile_key == "staff_123:default"

    def test_protocol_get_nonexistent_returns_none(self):
        """Test get returns None for nonexistent key."""
        mock_store = MockMetadataStore()

        result = mock_store.get("nonexistent")

        assert result is None

    def test_protocol_get_by_vector_ids_method(self):
        """Test get_by_vector_ids method signature."""
        mock_store = MockMetadataStore()

        records = [
            MetadataRecord(
                profile_key="staff_123:default",
                vector_id=42,
                staff_id="123",
                profile_id="default",
                profile_type="default",
                domains=["backend"],
                active_skill_names=["python"],
                suitable_roles=["developer"],
                source_root="/data"
            )
        ]
        mock_store.upsert(records)

        results = mock_store.get_by_vector_ids([42])

        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0].vector_id == 42

    def test_protocol_filter_method_no_filters(self):
        """Test filter method without filters returns all records."""
        mock_store = MockMetadataStore()

        records = [
            MetadataRecord(
                profile_key="staff_123:default",
                staff_id="123",
                profile_id="default",
                profile_type="default",
                domains=["backend"],
                active_skill_names=["python"],
                suitable_roles=["developer"],
                source_root="/data"
            ),
            MetadataRecord(
                profile_key="staff_456:default",
                staff_id="456",
                profile_id="default",
                profile_type="default",
                domains=["frontend"],
                active_skill_names=["javascript"],
                suitable_roles=["developer"],
                source_root="/data"
            ),
        ]
        mock_store.upsert(records)

        results = mock_store.filter()

        assert isinstance(results, list)
        assert len(results) == 2

    def test_protocol_filter_method_with_filters(self):
        """Test filter method with filters."""
        mock_store = MockMetadataStore()

        records = [
            MetadataRecord(
                profile_key="staff_123:default",
                staff_id="123",
                profile_id="default",
                profile_type="default",
                domains=["backend"],
                active_skill_names=["python"],
                suitable_roles=["developer"],
                source_root="/data"
            ),
            MetadataRecord(
                profile_key="staff_456:default",
                staff_id="456",
                profile_id="default",
                profile_type="bot",
                domains=["frontend"],
                active_skill_names=["javascript"],
                suitable_roles=["developer"],
                source_root="/data"
            ),
        ]
        mock_store.upsert(records)

        # Filter by profile_type
        results = mock_store.filter({"profile_type": "default"})

        assert len(results) == 1
        assert results[0].profile_type == "default"

    def test_protocol_filter_method_domains_or_semantics(self):
        """Test filter with domains uses OR semantics."""
        mock_store = MockMetadataStore()

        records = [
            MetadataRecord(
                profile_key="staff_123:default",
                staff_id="123",
                profile_id="default",
                profile_type="default",
                domains=["backend"],
                active_skill_names=["python"],
                suitable_roles=["developer"],
                source_root="/data"
            ),
            MetadataRecord(
                profile_key="staff_456:default",
                staff_id="456",
                profile_id="default",
                profile_type="default",
                domains=["frontend"],
                active_skill_names=["javascript"],
                suitable_roles=["developer"],
                source_root="/data"
            ),
            MetadataRecord(
                profile_key="staff_789:default",
                staff_id="789",
                profile_id="default",
                profile_type="default",
                domains=["devops"],
                active_skill_names=["kubernetes"],
                suitable_roles=["developer"],
                source_root="/data"
            ),
        ]
        mock_store.upsert(records)

        # Filter: backend OR frontend
        results = mock_store.filter({"domains": ["backend", "frontend"]})

        assert len(results) == 2
        domains_in_results = [d for r in results for d in r.domains]
        assert "backend" in domains_in_results or "frontend" in domains_in_results

    def test_protocol_delete_method(self):
        """Test delete method signature."""
        mock_store = MockMetadataStore()

        records = [
            MetadataRecord(
                profile_key="staff_123:default",
                staff_id="123",
                profile_id="default",
                profile_type="default",
                domains=["backend"],
                active_skill_names=["python"],
                suitable_roles=["developer"],
                source_root="/data"
            )
        ]
        mock_store.upsert(records)

        # Should not raise
        mock_store.delete(["staff_123:default"])

        assert mock_store.size() == 0

    def test_protocol_size_method(self):
        """Test size method signature."""
        mock_store = MockMetadataStore()

        assert mock_store.size() == 0

        records = [
            MetadataRecord(
                profile_key="staff_123:default",
                staff_id="123",
                profile_id="default",
                profile_type="default",
                domains=["backend"],
                active_skill_names=["python"],
                suitable_roles=["developer"],
                source_root="/data"
            )
        ]
        mock_store.upsert(records)

        assert mock_store.size() == 1

    def test_protocol_save_load_methods(self):
        """Test save/load method signatures."""
        mock_store = MockMetadataStore()

        # Should not raise
        mock_store.save("/tmp/test_metadata")
        mock_store.load("/tmp/test_metadata")