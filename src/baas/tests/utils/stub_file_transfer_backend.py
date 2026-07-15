"""Stub file transfer backend for E2E testing.

In-memory dict implementation of FileTransferBackend Protocol.
Uses fake stub-upload:// and stub-download:// URLs so tests can inject
and extract file content without a real OSS instance.

Protocol methods (matching secbaas.spi.file_transfer.FileTransferBackend):
- generate_upload_url(staging_path, expire_seconds) -> str
- check_object_exists(staging_path) -> bool
- generate_download_url(staging_path, expire_seconds) -> str
- initiate_multipart_upload(staging_path, expire_seconds, part_count=2) -> MultipartSession
- list_parts(staging_path, session_id) -> list[PartInfo]
- complete_multipart_upload(staging_path, session_id, parts) -> None
- abort_multipart_upload(staging_path, session_id) -> None
- list_objects(prefix, limit, marker) -> ObjectListing
- delete_object(key) -> None

Test helper methods:
- put_content(url: str, data: bytes) -> None
- get_content(url: str) -> bytes
- put_multipart_content(transfer_id, data, part_number=1) -> None
"""

from __future__ import annotations

from secbaas.spi.file_transfer import (
    FileTransferBackend,
    MultipartSession,
    ObjectItem,
    ObjectListing,
    PartInfo,
)


class StubFileTransferBackend(FileTransferBackend):
    """In-memory stub implementing FileTransferBackend Protocol.

    Uses a dict[str, bytes] keyed by transfer_id to simulate OSS storage.
    Provides put_content/get_content helpers for test-side file simulation.

    .. note::

        Storage keys are bare transfer IDs (extracted from staging paths),
        **not** full OSS-style paths.  ``list_objects`` filters against these
        bare keys, which means prefix queries using tenant-scoped staging-path
        prefixes (e.g. ``"baas-file-transfer/t1/"``) will return empty results.
        Tests that exercise ``list_objects`` should seed storage via
        ``put_content`` with transfer-ID URLs, or use bare transfer IDs as
        prefixes.
    """

    def __init__(self, staging_root_path: str = "baas-file-transfer") -> None:
        self._storage: dict[str, bytes] = {}
        self._multipart_sessions: dict[str, dict] = {}
        self._staging_root_path = staging_root_path

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

    # ── Phase 72: Multipart upload methods ────────────────────────────

    def initiate_multipart_upload(
        self, staging_path: str, expire_seconds: int, part_count: int = 2,
    ) -> MultipartSession:
        """Initiate a stub multipart upload session.

        Always returns 2 parts for testing simplicity.  E2E tests use
        small data so 2 parts is sufficient.

        Args:
            staging_path: OSS object key (ignored — transfer_id extracted).
            expire_seconds: URL validity duration in seconds (ignored by stub).
            part_count: Number of parts to generate (default 2 for E2E tests).

        Returns:
            MultipartSession with stub-mp-* prefixed session_id and pre-signed URLs.
        """
        transfer_id = self._extract_transfer_id(staging_path)
        session_id = f"stub-mp-{transfer_id}"
        parts = []
        for i in range(1, part_count + 1):
            parts.append(
                PartInfo(
                    part_number=i,
                    upload_url=f"stub-mp-upload://{transfer_id}/{i}",
                )
            )
        self._multipart_sessions[transfer_id] = {
            "session_id": session_id,
            "parts": parts,
            "uploaded_parts": {},
        }
        return MultipartSession(
            session_id=session_id, part_count=part_count, parts=parts,
        )

    def list_parts(self, staging_path: str, session_id: str) -> list[PartInfo]:
        """Return uploaded parts with synthetic ETags.

        Args:
            staging_path: OSS object key (ignored — transfer_id extracted).
            session_id: Multipart upload session ID.

        Returns:
            List of PartInfo for parts that have been uploaded.
        """
        transfer_id = self._extract_transfer_id(staging_path)
        session = self._multipart_sessions.get(transfer_id, {})
        uploaded_parts = session.get("uploaded_parts", {})
        return [
            PartInfo(part_number=pn, upload_url="", etag=f"etag-{pn}")
            for pn in sorted(uploaded_parts.keys())
        ]

    def complete_multipart_upload(
        self, staging_path: str, session_id: str, parts: list[PartInfo],
    ) -> None:
        """Assemble uploaded parts into the final file in _storage.

        Args:
            staging_path: OSS object key (ignored — transfer_id extracted).
            session_id: Multipart upload session ID.
            parts: List of PartInfo from list_parts.
        """
        transfer_id = self._extract_transfer_id(staging_path)
        session = self._multipart_sessions.get(transfer_id, {})
        uploaded_parts = session.get("uploaded_parts", {})
        # Concatenate all uploaded parts in order
        assembled = bytearray()
        for pn in sorted(uploaded_parts.keys()):
            assembled.extend(uploaded_parts[pn])
        self._storage[transfer_id] = bytes(assembled)

    def abort_multipart_upload(self, staging_path: str, session_id: str) -> None:
        """Cancel an in-progress multipart upload, clearing session state.

        Args:
            staging_path: OSS object key (ignored — transfer_id extracted).
            session_id: Multipart upload session ID.
        """
        transfer_id = self._extract_transfer_id(staging_path)
        self._multipart_sessions.pop(transfer_id, None)

    # ── Phase 72: Staging object management methods ───────────────────

    def list_objects(
        self, prefix: str, limit: int, marker: str | None,
    ) -> ObjectListing:
        """List storage keys matching a prefix with marker pagination.

        Args:
            prefix: Key prefix to filter by.
            limit: Maximum number of items to return.
            marker: Opaque pagination marker (last key from previous page).

        Returns:
            ObjectListing with items, truncated flag, and next_marker.
        """
        # Collect all matching keys, sorted
        matching = sorted(
            [k for k in self._storage.keys() if k.startswith(prefix)]
        )
        # Apply marker-based pagination
        start_idx = 0
        if marker is not None:
            for i, key in enumerate(matching):
                if key > marker:
                    start_idx = i
                    break
            else:
                start_idx = len(matching)

        page = matching[start_idx : start_idx + limit]
        truncated = (start_idx + limit) < len(matching)
        next_marker = page[-1] if truncated and page else None

        items = [
            ObjectItem(
                key=k,
                size=len(self._storage[k]),
                last_modified="",
            )
            for k in page
        ]
        return ObjectListing(items=items, truncated=truncated, next_marker=next_marker)

    def delete_object(self, key: str) -> None:
        """Delete a single object from storage by key.

        Args:
            key: Full storage key to delete.
        """
        self._storage.pop(key, None)

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

    def put_multipart_content(
        self, transfer_id: str, data: bytes, part_number: int = 1,
    ) -> None:
        """Simulate uploading a single part for a multipart upload.

        Args:
            transfer_id: The transfer_id used in initiate_multipart_upload.
            data: Raw bytes for this part.
            part_number: 1-based part number (default 1).
        """
        if transfer_id not in self._multipart_sessions:
            self._multipart_sessions[transfer_id] = {
                "session_id": f"stub-mp-{transfer_id}",
                "parts": [],
                "uploaded_parts": {},
            }
        self._multipart_sessions[transfer_id]["uploaded_parts"][part_number] = data

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
        if len(parts) >= 3 and parts[0] in ("file-transfers", "baas-file-transfer", self._staging_root_path):
            return parts[-2]
        return staging_path  # fallback

    def build_staging_path(
        self,
        tenant: str,
        transfer_id: str,
        filename: str,
        subdir: str | None = None,
    ) -> str:
        """Construct full OSS object key for file transfer staging.

        The Dispatcher calls this instead of hardcoding paths.
        Pattern: baas-file-transfer/{tenant}[/{subdir}]/{transfer_id}/{filename}

        Args:
            tenant: Tenant identifier for scoping.
            transfer_id: Transfer ticket ID for uniqueness.
            filename: Target filename on the OSS object.
            subdir: Optional subdirectory under the tenant scope.

        Returns:
            Complete OSS object key string.
        """
        root = self._staging_root_path
        subdir_part = f"{subdir}/" if subdir else ""
        return f"{root}/{tenant}/{subdir_part}{transfer_id}/{filename}"

    def build_staging_prefix(
        self, tenant: str, subdir: str | None = None,
    ) -> str:
        """Construct OSS key prefix for tenant-scoped object listing.

        Used by list_staging to scope results to a single tenant.
        The returned prefix ends with "/".

        Args:
            tenant: Tenant identifier for scoping.
            subdir: Optional subdirectory under the tenant scope.

        Returns:
            Prefix string ending with "/".
        """
        root = self._staging_root_path
        subdir_part = f"{subdir}/" if subdir else ""
        return f"{root}/{tenant}/{subdir_part}"
    def _parse_url_transfer_id(self, url: str) -> str:
        """Extract transfer_id from a fake stub URL.

        Args:
            url: Fake URL (``stub-upload://{id}`` or ``stub-download://{id}``).

        Returns:
            The transfer_id string after the ``://`` separator.

        Raises:
            ValueError: If the URL does not start with a recognised stub prefix.
        """
        for prefix in ("stub-upload://", "stub-download://", "stub-mp-upload://"):
            if url.startswith(prefix):
                rest = url[len(prefix):]
                # multipart URLs have format: {transfer_id}/{part_number}
                return rest.split("/", 1)[0]
        raise ValueError(f"Unrecognized stub URL: {url}")