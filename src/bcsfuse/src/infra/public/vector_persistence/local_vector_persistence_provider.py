"""Local file system vector persistence provider for public open-core.

This provider implements VectorPersistenceProvider using local file system
for local development and testing. It does not require any internal dependencies
(ZDAS, OceanBase).

For internal production, use ZdasVectorPersistenceProvider from bcsfuse-internal.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.application.ports.vector_persistence_provider import VectorPersistenceProvider


logger = logging.getLogger(__name__)


class LocalVectorPersistenceProvider:
    """Local file system implementation of VectorPersistenceProvider.

    This provider stores vectors as JSON files on the local file system.
    Suitable for development, testing, and small-scale OSS deployments.

    Features:
    - Simple JSON-based storage
    - Collection-based organization
    - Backup and restore support
    - No internal dependencies
    """

    def __init__(
        self,
        storage_dir: str = "data/vectors",
        enable_logging: bool = True
    ):
        """Initialize local vector persistence provider.

        Args:
            storage_dir: Root directory for vector storage
            enable_logging: Enable operation logging for debugging
        """
        self._storage_dir = Path(storage_dir)
        self._enable_logging = enable_logging
        self._closed = False

        # Create storage directory
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        if self._enable_logging:
            logger.info(f"[LocalVectorPersistence] Initialized at {self._storage_dir}")

    def _get_collection_dir(self, collection_name: str) -> Path:
        """Get directory path for a collection.

        Args:
            collection_name: Name of the collection

        Returns:
            Path to collection directory
        """
        return self._storage_dir / collection_name

    def _get_vectors_file(self, collection_name: str) -> Path:
        """Get vectors file path for a collection.

        Args:
            collection_name: Name of the collection

        Returns:
            Path to vectors.json file
        """
        return self._get_collection_dir(collection_name) / "vectors.json"

    def _get_metadata_file(self, collection_name: str) -> Path:
        """Get metadata file path for a collection.

        Args:
            collection_name: Name of the collection

        Returns:
            Path to metadata.json file
        """
        return self._get_collection_dir(collection_name) / "metadata.json"

    def save_vectors(
        self,
        collection_name: str,
        vectors: List[Dict[str, Any]]
    ) -> bool:
        """Save vectors to persistent storage.

        Args:
            collection_name: Name of the vector collection
            vectors: List of vector dicts

        Returns:
            True if save successful, False otherwise
        """
        try:
            collection_dir = self._get_collection_dir(collection_name)
            collection_dir.mkdir(parents=True, exist_ok=True)

            vectors_file = self._get_vectors_file(collection_name)

            # Load existing vectors
            existing_vectors = []
            if vectors_file.exists():
                with open(vectors_file, "r") as f:
                    existing_vectors = json.load(f)

            # Build ID -> vector map for deduplication
            vector_map = {v["id"]: v for v in existing_vectors}
            for v in vectors:
                vector_map[v["id"]] = v

            # Save merged vectors
            merged_vectors = list(vector_map.values())
            with open(vectors_file, "w") as f:
                json.dump(merged_vectors, f, indent=2)

            # Update metadata
            metadata = {
                "collection_name": collection_name,
                "vector_count": len(merged_vectors),
                "last_updated": str(Path(vectors_file).stat().st_mtime),
            }

            if merged_vectors:
                first_vector = merged_vectors[0]
                if "vector" in first_vector:
                    metadata["dimension"] = len(first_vector["vector"])

            metadata_file = self._get_metadata_file(collection_name)
            with open(metadata_file, "w") as f:
                json.dump(metadata, f, indent=2)

            if self._enable_logging:
                logger.info(
                    f"[LocalVectorPersistence] Saved {len(vectors)} vectors to {collection_name} "
                    f"(total: {len(merged_vectors)})"
                )

            return True

        except Exception as e:
            logger.error(f"[LocalVectorPersistence] Failed to save vectors to {collection_name}: {e}")
            return False

    def load_vectors(
        self,
        collection_name: str,
        filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Load vectors from persistent storage.

        Args:
            collection_name: Name of the vector collection
            filter: Optional metadata filter

        Returns:
            List of vector dicts
        """
        try:
            vectors_file = self._get_vectors_file(collection_name)

            if not vectors_file.exists():
                if self._enable_logging:
                    logger.debug(f"[LocalVectorPersistence] Collection {collection_name} not found")
                return []

            with open(vectors_file, "r") as f:
                vectors = json.load(f)

            # Apply filter if provided
            if filter:
                filtered_vectors = []
                for v in vectors:
                    metadata = v.get("metadata", {})
                    match = True
                    for key, value in filter.items():
                        if metadata.get(key) != value:
                            match = False
                            break
                    if match:
                        filtered_vectors.append(v)
                vectors = filtered_vectors

            if self._enable_logging:
                logger.debug(f"[LocalVectorPersistence] Loaded {len(vectors)} vectors from {collection_name}")

            return vectors

        except Exception as e:
            logger.error(f"[LocalVectorPersistence] Failed to load vectors from {collection_name}: {e}")
            return []

    def delete_vectors(
        self,
        collection_name: str,
        vector_ids: Optional[List[str]] = None,
        filter: Optional[Dict[str, Any]] = None
    ) -> int:
        """Delete vectors from persistent storage.

        Args:
            collection_name: Name of the vector collection
            vector_ids: List of vector IDs to delete (optional)
            filter: Metadata filter for vectors to delete (optional)

        Returns:
            Number of vectors deleted
        """
        try:
            vectors_file = self._get_vectors_file(collection_name)

            if not vectors_file.exists():
                return 0

            with open(vectors_file, "r") as f:
                vectors = json.load(f)

            original_count = len(vectors)

            # Filter out vectors to delete
            if vector_ids:
                id_set = set(vector_ids)
                vectors = [v for v in vectors if v["id"] not in id_set]
            elif filter:
                filtered_vectors = []
                for v in vectors:
                    metadata = v.get("metadata", {})
                    match = True
                    for key, value in filter.items():
                        if metadata.get(key) != value:
                            match = False
                            break
                    if not match:
                        filtered_vectors.append(v)
                vectors = filtered_vectors

            deleted_count = original_count - len(vectors)

            # Save updated vectors
            with open(vectors_file, "w") as f:
                json.dump(vectors, f, indent=2)

            # Update metadata
            metadata_file = self._get_metadata_file(collection_name)
            if metadata_file.exists():
                with open(metadata_file, "r") as f:
                    metadata = json.load(f)
                metadata["vector_count"] = len(vectors)
                with open(metadata_file, "w") as f:
                    json.dump(metadata, f, indent=2)

            if self._enable_logging:
                logger.info(f"[LocalVectorPersistence] Deleted {deleted_count} vectors from {collection_name}")

            return deleted_count

        except Exception as e:
            logger.error(f"[LocalVectorPersistence] Failed to delete vectors from {collection_name}: {e}")
            return 0

    def list_collections(self) -> List[str]:
        """List all vector collections in storage.

        Returns:
            List of collection names
        """
        try:
            collections = []
            for item in self._storage_dir.iterdir():
                if item.is_dir() and (item / "vectors.json").exists():
                    collections.append(item.name)

            if self._enable_logging:
                logger.debug(f"[LocalVectorPersistence] Found {len(collections)} collections")

            return sorted(collections)

        except Exception as e:
            logger.error(f"[LocalVectorPersistence] Failed to list collections: {e}")
            return []

    def delete_collection(self, collection_name: str) -> bool:
        """Delete an entire vector collection.

        Args:
            collection_name: Name of the collection to delete

        Returns:
            True if deletion successful, False otherwise
        """
        try:
            collection_dir = self._get_collection_dir(collection_name)

            if not collection_dir.exists():
                if self._enable_logging:
                    logger.debug(f"[LocalVectorPersistence] Collection {collection_name} not found")
                return False

            shutil.rmtree(collection_dir)

            if self._enable_logging:
                logger.info(f"[LocalVectorPersistence] Deleted collection {collection_name}")

            return True

        except Exception as e:
            logger.error(f"[LocalVectorPersistence] Failed to delete collection {collection_name}: {e}")
            return False

    def collection_exists(self, collection_name: str) -> bool:
        """Check if a collection exists.

        Args:
            collection_name: Name of the collection

        Returns:
            True if collection exists, False otherwise
        """
        collection_dir = self._get_collection_dir(collection_name)
        vectors_file = self._get_vectors_file(collection_name)
        return collection_dir.exists() and vectors_file.exists()

    def get_collection_stats(self, collection_name: str) -> Dict[str, Any]:
        """Get statistics about a vector collection.

        Args:
            collection_name: Name of the collection

        Returns:
            Dict with stats
        """
        try:
            metadata_file = self._get_metadata_file(collection_name)

            if not metadata_file.exists():
                return {
                    "vector_count": 0,
                    "dimension": 0,
                    "exists": False,
                }

            with open(metadata_file, "r") as f:
                metadata = json.load(f)

            metadata["exists"] = True
            return metadata

        except Exception as e:
            logger.error(f"[LocalVectorPersistence] Failed to get stats for {collection_name}: {e}")
            return {
                "vector_count": 0,
                "dimension": 0,
                "exists": False,
                "error": str(e),
            }

    def backup_collection(
        self,
        collection_name: str,
        backup_path: str
    ) -> bool:
        """Backup a vector collection to a file path.

        Args:
            collection_name: Name of the collection to backup
            backup_path: File path for backup

        Returns:
            True if backup successful, False otherwise
        """
        try:
            collection_dir = self._get_collection_dir(collection_name)

            if not collection_dir.exists():
                logger.error(f"[LocalVectorPersistence] Collection {collection_name} not found for backup")
                return False

            backup_file = Path(backup_path)
            backup_file.parent.mkdir(parents=True, exist_ok=True)

            # Create zip archive
            shutil.make_archive(
                str(backup_file.with_suffix("")),
                "zip",
                collection_dir.parent,
                collection_dir.name
            )

            if self._enable_logging:
                logger.info(f"[LocalVectorPersistence] Backed up {collection_name} to {backup_path}")

            return True

        except Exception as e:
            logger.error(f"[LocalVectorPersistence] Failed to backup {collection_name}: {e}")
            return False

    def restore_collection(
        self,
        collection_name: str,
        backup_path: str
    ) -> bool:
        """Restore a vector collection from a backup file.

        Args:
            collection_name: Name of the collection to restore
            backup_path: File path for backup

        Returns:
            True if restore successful, False otherwise
        """
        try:
            backup_file = Path(backup_path)

            if not backup_file.exists():
                logger.error(f"[LocalVectorPersistence] Backup file not found: {backup_path}")
                return False

            collection_dir = self._get_collection_dir(collection_name)

            # Remove existing collection if exists
            if collection_dir.exists():
                shutil.rmtree(collection_dir)

            # Extract backup
            shutil.unpack_archive(
                str(backup_file),
                collection_dir.parent,
                "zip"
            )

            if self._enable_logging:
                logger.info(f"[LocalVectorPersistence] Restored {collection_name} from {backup_path}")

            return True

        except Exception as e:
            logger.error(f"[LocalVectorPersistence] Failed to restore {collection_name}: {e}")
            return False

    def __repr__(self) -> str:
        """String representation."""
        return f"LocalVectorPersistenceProvider(storage_dir={self._storage_dir}, closed={self._closed})"