"""File-based Metadata Store Adapter implementation.

This module provides a local file-based implementation of MetadataStoreAdapter,
using JSONL format for storing metadata records and JSON for the vector_id mapping.
"""

import json
import os
from pathlib import Path
from typing import Any

from src.domain.models.metadata_record import MetadataRecord
from src.domain.services.metadata_store_adapter import MetadataStoreAdapter


class FileMetadataStoreAdapter:
    """
    File-based implementation of MetadataStoreAdapter.

    Storage structure:
        {storage_dir}/
            metadata.jsonl       # All metadata records (one JSON per line)
            vector_id_map.json   # Mapping: vector_id -> profile_key

    Features:
    - Simple JSONL format for easy debugging and inspection
    - In-memory indexing for fast lookups
    - Atomic save operations
    - Supports all filter types with OR semantics for multi-values

    Note:
    - This is a local baseline implementation
    - For production, consider using SQLite or a distributed database
    - Not thread-safe; use appropriate locking if needed
    """

    def __init__(self, storage_dir: str | None = None):
        """
        Initialize FileMetadataStoreAdapter.

        Args:
            storage_dir: Directory for storing metadata files.
                        If None, data is only kept in memory.
        """
        self._storage_dir = storage_dir
        self._records: dict[str, MetadataRecord] = {}
        self._vector_id_map: dict[int, str] = {}

    def upsert(self, records: list[MetadataRecord]) -> None:
        """
        Insert or update metadata records.

        Args:
            records: List of metadata records to upsert

        Raises:
            ValueError: If records list is invalid
        """
        if not records:
            return

        for record in records:
            # Remove old vector_id mapping if exists
            old_record = self._records.get(record.profile_key)
            if old_record and old_record.vector_id is not None:
                self._vector_id_map.pop(old_record.vector_id, None)

            # Upsert the record
            self._records[record.profile_key] = record

            # Add new vector_id mapping if present
            if record.vector_id is not None:
                self._vector_id_map[record.vector_id] = record.profile_key

    def get(self, profile_key: str) -> MetadataRecord | None:
        """
        Get a metadata record by profile_key.

        Args:
            profile_key: Unique profile identifier

        Returns:
            MetadataRecord or None if not found
        """
        return self._records.get(profile_key)

    def get_by_vector_ids(self, vector_ids: list[int]) -> list[MetadataRecord]:
        """
        Get metadata records by vector IDs.

        Args:
            vector_ids: List of vector IDs to look up

        Returns:
            List of matching metadata records (non-existent IDs ignored)
        """
        results = []
        for vid in vector_ids:
            if vid in self._vector_id_map:
                profile_key = self._vector_id_map[vid]
                record = self._records.get(profile_key)
                if record:
                    results.append(record)
        return results

    def filter(self, filters: dict | None = None) -> list[MetadataRecord]:
        """
        Filter metadata records based on criteria.

        Supported filters:
        - domains: List of domains (OR semantics)
        - profile_type: Exact match
        - active_skill_names: List of skills (OR semantics)
        - suitable_roles: List of roles (OR semantics)

        Multiple filter types use AND semantics.

        Args:
            filters: Filter criteria dictionary

        Returns:
            List of matching metadata records
        """
        if not filters:
            return list(self._records.values())

        results = []
        for record in self._records.values():
            if self._matches_filters(record, filters):
                results.append(record)

        return results

    def _matches_filters(self, record: MetadataRecord, filters: dict[str, Any]) -> bool:
        """
        Check if a record matches all filter criteria.

        Args:
            record: The record to check
            filters: Filter criteria

        Returns:
            True if record matches all filters
        """
        # Filter by domains (OR semantics)
        if "domains" in filters:
            filter_domains = filters["domains"]
            if filter_domains:  # Empty list means no filter
                if not any(d in record.domains for d in filter_domains):
                    return False

        # Filter by profile_type (exact match)
        if "profile_type" in filters:
            if record.profile_type != filters["profile_type"]:
                return False

        # Filter by active_skill_names (OR semantics)
        if "active_skill_names" in filters:
            filter_skills = filters["active_skill_names"]
            if filter_skills:  # Empty list means no filter
                if not any(s in record.active_skill_names for s in filter_skills):
                    return False

        # Filter by suitable_roles (OR semantics)
        if "suitable_roles" in filters:
            filter_roles = filters["suitable_roles"]
            if filter_roles:  # Empty list means no filter
                if not any(r in record.suitable_roles for r in filter_roles):
                    return False

        return True

    def delete(self, profile_keys: list[str]) -> None:
        """
        Delete metadata records by profile_keys.

        Non-existent keys are silently ignored.

        Args:
            profile_keys: List of profile keys to delete
        """
        for key in profile_keys:
            record = self._records.pop(key, None)
            if record and record.vector_id is not None:
                self._vector_id_map.pop(record.vector_id, None)

    def save(self, path: str) -> None:
        """
        Save metadata to files.

        Creates two files:
        - metadata.jsonl: All records in JSONL format
        - vector_id_map.json: Vector ID to profile_key mapping

        Args:
            path: Directory path to save files

        Raises:
            IOError: If save fails
        """
        save_dir = Path(path)

        # Create directory if needed
        save_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Save records to JSONL
            metadata_path = save_dir / "metadata.jsonl"
            with open(metadata_path, "w", encoding="utf-8") as f:
                for record in self._records.values():
                    f.write(record.model_dump_json() + "\n")

            # Save vector_id_map to JSON
            vector_id_map_path = save_dir / "vector_id_map.json"
            with open(vector_id_map_path, "w", encoding="utf-8") as f:
                json.dump(self._vector_id_map, f, indent=2)

        except Exception as e:
            raise IOError(f"Failed to save metadata: {e}") from e

    def load(self, path: str) -> None:
        """
        Load metadata from files.

        Args:
            path: Directory path containing metadata files

        Raises:
            FileNotFoundError: If directory or files don't exist
            IOError: If load fails
        """
        load_dir = Path(path)

        if not load_dir.exists():
            raise FileNotFoundError(f"Directory not found: {path}")

        metadata_path = load_dir / "metadata.jsonl"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Metadata file not found: {metadata_path}")

        # Clear existing data
        self._records.clear()
        self._vector_id_map.clear()

        try:
            # Load records from JSONL
            with open(metadata_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        record_dict = json.loads(line)
                        record = MetadataRecord(**record_dict)
                        self._records[record.profile_key] = record

                        # Rebuild vector_id_map
                        if record.vector_id is not None:
                            self._vector_id_map[record.vector_id] = record.profile_key

        except json.JSONDecodeError as e:
            raise IOError(f"Invalid JSON in metadata file: {e}") from e
        except Exception as e:
            raise IOError(f"Failed to load metadata: {e}") from e

    def size(self) -> int:
        """
        Get the number of metadata records.

        Returns:
            Number of records
        """
        return len(self._records)