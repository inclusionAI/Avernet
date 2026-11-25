"""
Local Runtime Object Storage Provider

Filesystem-based object storage for OSS deployments.
"""
import os
import hashlib
from pathlib import Path
from typing import Optional
from datetime import datetime


class LocalRuntimeObjectStorageProvider:
    """
    Local filesystem-based object storage provider.

    Suitable for OSS deployments without cloud object storage.
    Stores objects in a local directory structure.

    IMPORTANT:
    - root_dir must be outside source code directories
    - Default: ./data/object_storage (runtime data directory)
    - NEVER stores tokens, passwords, or secrets
    - Used for: uploaded files, imported files, reports, exports
    - NOT used for: worker registry, profile core structured data, vector embeddings, audit logs
    """

    def __init__(self, root_dir: Optional[str] = None):
        """Initialize object storage provider.

        Args:
            root_dir: Root directory for object storage.
                     If None, uses BCSFUSE_OBJECT_STORAGE_DIR env var
                     or defaults to ./data/object_storage.
        """
        if root_dir is None:
            root_dir = os.getenv("BCSFUSE_OBJECT_STORAGE_DIR", "./data/object_storage")

        self.root_dir = Path(root_dir).resolve()

        # Validate root_dir is not in current source tree
        self._validate_root_dir()

        # Create root directory if it doesn't exist
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _validate_root_dir(self) -> None:
        """Validate that root_dir is not in current source tree."""
        root_str = str(self.root_dir)
        cwd_str = str(Path.cwd())

        # Check if root_dir is under the current working directory's src/ directory
        # This prevents accidentally storing data in source code
        src_dir = Path.cwd() / "src"
        if src_dir.exists():
            try:
                # Check if root_dir is a subdirectory of src/
                self.root_dir.relative_to(src_dir)
                raise ValueError(
                    f"Object storage root_dir cannot be inside src/ directory: {self.root_dir}"
                )
            except ValueError:
                # root_dir is not under src/, which is good
                pass

    def _get_object_path(self, key: str) -> Path:
        """Get filesystem path for object key.

        Uses hash-based subdirectories to avoid too many files in one directory.
        """
        # Create hash-based subdirectory for better filesystem performance
        key_hash = hashlib.md5(key.encode()).hexdigest()[:3]
        subdir = self.root_dir / key_hash
        subdir.mkdir(exist_ok=True)

        # Sanitize key for filesystem
        safe_key = key.replace("/", "_").replace("\\", "_")
        return subdir / safe_key

    def upload(self, key: str, data: bytes, content_type: Optional[str] = None) -> bool:
        """Upload object to storage.

        Args:
            key: Object key (identifier).
            data: Object data as bytes.
            content_type: Optional content type (stored as metadata).

        Returns:
            True if successful, False otherwise.
        """
        try:
            object_path = self._get_object_path(key)

            # Write object data
            with open(object_path, "wb") as f:
                f.write(data)

            # Write metadata
            metadata_path = object_path.with_suffix(".meta")
            metadata = {
                "key": key,
                "size": len(data),
                "content_type": content_type or "application/octet-stream",
                "uploaded_at": datetime.utcnow().isoformat(),
            }
            with open(metadata_path, "w", encoding="utf-8") as f:
                import json
                json.dump(metadata, f, indent=2)

            return True
        except Exception as e:
            print(f"Error uploading object {key}: {e}")
            return False

    def download(self, key: str) -> Optional[bytes]:
        """Download object from storage.

        Args:
            key: Object key.

        Returns:
            Object data if exists, None otherwise.
        """
        try:
            object_path = self._get_object_path(key)

            if not object_path.exists():
                return None

            with open(object_path, "rb") as f:
                return f.read()
        except Exception as e:
            print(f"Error downloading object {key}: {e}")
            return None

    def delete(self, key: str) -> bool:
        """Delete object from storage.

        Args:
            key: Object key.

        Returns:
            True if successful, False otherwise.
        """
        try:
            object_path = self._get_object_path(key)
            metadata_path = object_path.with_suffix(".meta")

            # Delete object
            if object_path.exists():
                object_path.unlink()

            # Delete metadata
            if metadata_path.exists():
                metadata_path.unlink()

            return True
        except Exception as e:
            print(f"Error deleting object {key}: {e}")
            return False

    def exists(self, key: str) -> bool:
        """Check if object exists in storage.

        Args:
            key: Object key.

        Returns:
            True if object exists, False otherwise.
        """
        object_path = self._get_object_path(key)
        return object_path.exists()

    def get_url(self, key: str, expires_in: int = 3600) -> Optional[str]:
        """Get URL for object (not supported for local storage).

        Args:
            key: Object key.
            expires_in: URL expiration time in seconds (ignored).

        Returns:
            None (local storage doesn't support URLs).
        """
        # Local storage doesn't support presigned URLs
        # Return None or file path depending on use case
        return None

    def get_metadata(self, key: str) -> Optional[dict]:
        """Get object metadata.

        Args:
            key: Object key.

        Returns:
            Metadata dict if exists, None otherwise.
        """
        try:
            object_path = self._get_object_path(key)
            metadata_path = object_path.with_suffix(".meta")

            if not metadata_path.exists():
                return None

            with open(metadata_path, "r", encoding="utf-8") as f:
                import json
                return json.load(f)
        except Exception as e:
            print(f"Error getting metadata for object {key}: {e}")
            return None

    def list_objects(self, prefix: Optional[str] = None) -> list[str]:
        """List objects in storage.

        Args:
            prefix: Optional key prefix to filter objects.

        Returns:
            List of object keys.
        """
        objects = []

        try:
            for subdir in self.root_dir.iterdir():
                if not subdir.is_dir():
                    continue

                for object_path in subdir.iterdir():
                    if object_path.suffix == ".meta":
                        continue

                    key = object_path.name
                    if prefix is None or key.startswith(prefix):
                        objects.append(key)
        except Exception as e:
            print(f"Error listing objects: {e}")

        return sorted(objects)

    def get_storage_stats(self) -> dict:
        """Get storage statistics.

        Returns:
            Dict with storage stats (total_size, object_count, etc).
        """
        total_size = 0
        object_count = 0

        try:
            for subdir in self.root_dir.iterdir():
                if not subdir.is_dir():
                    continue

                for object_path in subdir.iterdir():
                    if object_path.suffix == ".meta":
                        continue

                    if object_path.exists():
                        total_size += object_path.stat().st_size
                        object_count += 1
        except Exception as e:
            print(f"Error getting storage stats: {e}")

        return {
            "root_dir": str(self.root_dir),
            "total_size_bytes": total_size,
            "object_count": object_count,
        }