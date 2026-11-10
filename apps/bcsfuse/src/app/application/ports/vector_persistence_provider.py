"""Vector persistence provider contract for public open-core.

This port abstracts vector storage persistence to allow different implementations:
- Public: Local file system, SQLite
- Internal: ZDAS-backed storage

Public code must depend on this contract, not internal storage SDKs.
"""

from typing import Protocol, List, Optional, Dict, Any, runtime_checkable


@runtime_checkable
class VectorPersistenceProvider(Protocol):
    """Public vector persistence provider contract.

    Implementations may be OSS defaults (local file system, SQLite) or
    internal plugins (ZDAS-backed storage).

    This port focuses on persistence (save/load) operations, complementing
    the VectorStore port which focuses on query operations.

    Public code must depend on this contract, not internal storage SDKs.
    """

    def save_vectors(
        self,
        collection_name: str,
        vectors: List[Dict[str, Any]]
    ) -> bool:
        """Save vectors to persistent storage.

        Args:
            collection_name: Name of the vector collection
            vectors: List of vector dicts with format:
                {
                    "id": str,
                    "vector": List[float],
                    "metadata": dict (optional)
                }

        Returns:
            True if save successful, False otherwise.
        """
        ...

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
            List of vector dicts with format:
                {
                    "id": str,
                    "vector": List[float],
                    "metadata": dict
                }
        """
        ...

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
            Number of vectors deleted.
        """
        ...

    def list_collections(self) -> List[str]:
        """List all vector collections in storage.

        Returns:
            List of collection names.
        """
        ...

    def delete_collection(self, collection_name: str) -> bool:
        """Delete an entire vector collection.

        Args:
            collection_name: Name of the collection to delete

        Returns:
            True if deletion successful, False otherwise.
        """
        ...

    def collection_exists(self, collection_name: str) -> bool:
        """Check if a collection exists.

        Args:
            collection_name: Name of the collection

        Returns:
            True if collection exists, False otherwise.
        """
        ...

    def get_collection_stats(self, collection_name: str) -> Dict[str, Any]:
        """Get statistics about a vector collection.

        Args:
            collection_name: Name of the collection

        Returns:
            Dict with stats like:
                {
                    "vector_count": int,
                    "dimension": int,
                    "size_bytes": int (optional),
                    "created_at": str (optional)
                }
        """
        ...

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
            True if backup successful, False otherwise.
        """
        ...

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
            True if restore successful, False otherwise.
        """
        ...