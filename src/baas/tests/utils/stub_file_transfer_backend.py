"""Stub file transfer backend for E2E testing.

In-memory dict implementation of FileTransferBackend Protocol.
Uses fake stub-upload:// and stub-download:// URLs so tests can inject
and extract file content without a real OSS instance.

Protocol methods (matching secbaas.spi.file_transfer.FileTransferBackend):
- generate_upload_url(staging_path, expire_seconds) -> str
- check_object_exists(staging_path) -> bool
- generate_download_url(staging_path, expire_seconds) -> str

Test helper methods:
- put_content(url: str, data: bytes) -> None
- get_content(url: str) -> bytes
"""

from __future__ import annotations


class StubFileTransferBackend:
    """In-memory stub implementing FileTransferBackend Protocol.

    Uses a dict[str, bytes] keyed by transfer_id to simulate OSS storage.
    Provides put_content/get_content helpers for test-side file simulation.
    """

    def __init__(self) -> None:
        self._storage: dict[str, bytes] = {}

    # ── Protocol methods ────────────────────────────────────────────

    def generate_upload_url(self, staging_path: str, expire_seconds: int) -> str:
        """Generate a fake presigned PUT URL for the given staging path.

        Args:
            staging_path: OSS object key (e.g. ``file-transfers/{id}/{name}``).
            expire_seconds: URL validity duration in seconds (ignored by stub).

        Returns:
            Fake URL string: ``stub-upload://{transfer_id}``.
        """
        transfer_id = self._extract_transfer_id(staging_path)
        return f"stub-upload://{transfer_id}"

    def check_object_exists(self, staging_path: str) -> bool:
        """Check whether the object identified by staging_path has been stored.

        Args:
            staging_path: OSS object key (e.g. ``file-transfers/{id}/{name}``).

        Returns:
            True if put_content has been called for the extracted transfer_id.
        """
        transfer_id = self._extract_transfer_id(staging_path)
        return transfer_id in self._storage

    def generate_download_url(self, staging_path: str, expire_seconds: int) -> str:
        """Generate a fake presigned GET URL for the given staging path.

        Args:
            staging_path: OSS object key (e.g. ``file-transfers/{id}/{name}``).
            expire_seconds: URL validity duration in seconds (ignored by stub).

        Returns:
            Fake URL string: ``stub-download://{transfer_id}``.
        """
        transfer_id = self._extract_transfer_id(staging_path)
        return f"stub-download://{transfer_id}"

    # ── Test helper methods ─────────────────────────────────────────

    def put_content(self, url: str, data: bytes) -> None:
        """Simulate uploading file content via a stub URL.

        Args:
            url: Fake URL (``stub-upload://{id}`` or ``stub-download://{id}``).
            data: Raw file content bytes.

        Raises:
            ValueError: If the URL prefix is not recognised.
        """
        transfer_id = self._parse_url_transfer_id(url)
        self._storage[transfer_id] = data

    def get_content(self, url: str) -> bytes:
        """Simulate downloading file content via a stub URL.

        Args:
            url: Fake URL (``stub-upload://{id}`` or ``stub-download://{id}``).

        Returns:
            Stored bytes, or ``b""`` if the transfer_id is not found.

        Raises:
            ValueError: If the URL prefix is not recognised.
        """
        transfer_id = self._parse_url_transfer_id(url)
        return self._storage.get(transfer_id, b"")

    # ── Internal helpers ────────────────────────────────────────────

    def _extract_transfer_id(self, staging_path: str) -> str:
        """Extract transfer_id from a staging path.

        If the path starts with ``file-transfers/`` the second path component
        is returned.  Otherwise the full staging_path is returned as a fallback.

        Args:
            staging_path: OSS object key, e.g. ``file-transfers/abc123/data.csv``.

        Returns:
            The transfer_id portion of the path, e.g. ``abc123``.
        """
        parts = staging_path.split("/")
        if len(parts) >= 2 and parts[0] == "file-transfers":
            return parts[1]
        return staging_path  # fallback

    def _parse_url_transfer_id(self, url: str) -> str:
        """Extract transfer_id from a fake stub URL.

        Args:
            url: Fake URL (``stub-upload://{id}`` or ``stub-download://{id}``).

        Returns:
            The transfer_id string after the ``://`` separator.

        Raises:
            ValueError: If the URL does not start with a recognised stub prefix.
        """
        for prefix in ("stub-upload://", "stub-download://"):
            if url.startswith(prefix):
                return url[len(prefix):]
        raise ValueError(f"Unrecognized stub URL: {url}")