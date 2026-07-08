"""DeviceFileSystem -- file system access on a device (local or remote).

Abstracts the difference between local pathlib operations and remote
container-runtime HTTP file APIs. A plain ``core`` seam (not a plugin): the
per-provider implementations live alongside it under ``core/devices/services``
and are selected by the ``DeviceFileSystemResolver``.

Current implementations:
- ``core.devices.services.local_device_filesystem.LocalDeviceFileSystem``
- ``core.devices.services.baas_device_filesystem.BaasDeviceFileSystem``
- ``core.devices.services.teclaw_device_filesystem.TeclawDeviceFileSystem``
- ``core.devices.services.arca_device_filesystem.ArcaDeviceFileSystem``
"""

from typing import Any, Protocol


class FileTooLargeError(Exception):
    """Raised by ``read_file`` when ``enforce_download_limit`` is set and the impl
    has a size cap the file exceeds (the HTTP adapter maps it to 413).

    Only impls that load the whole file into memory (e.g. Arca) cap downloads; the
    rest ignore the flag.
    """


class DeviceFileSystem(Protocol):
    """Read / write / delete files on a device filesystem."""

    async def read_file(
        self, file_path: str, *, enforce_download_limit: bool = False
    ) -> bytes | None:
        """Read file content.

        Args:
            file_path: Absolute path on the device.
            enforce_download_limit: When True, an impl that loads the whole file into
                memory (Arca) checks the size **before** reading and raises
                :class:`FileTooLargeError` if it exceeds the impl's cap. Other impls
                ignore the flag.

        Returns:
            File content as bytes, or None if file does not exist.
        """
        ...

    async def write_file(self, file_path: str, content: bytes) -> None:
        """Write content to a file, creating parent directories as needed.

        Args:
            file_path: Absolute path on the device.
            content: File content as bytes.
        """
        ...

    async def delete_tree(self, dir_path: str) -> bool:
        """Recursively delete a directory.

        Args:
            dir_path: Absolute path of the directory to delete.

        Returns:
            True if deletion succeeded (or dir did not exist), False on error.
        """
        ...

    async def delete_file(self, file_path: str) -> bool:
        """Delete a single file.

        Args:
            file_path: Absolute path of the file to delete.

        Returns:
            True if the file was deleted or is already absent (idempotent),
            False on error.
        """
        ...

    async def list_dir(
        self, dir_path: str, *, recursive: bool = False
    ) -> list[dict[str, Any]] | None:
        """List directory contents.

        Args:
            dir_path: Absolute path of the directory.
            recursive: If True, list all descendants recursively.

        Returns:
            List of file info dicts, or None if directory does not exist.
        """
        ...

    async def exists(self, path: str) -> bool:
        """Check if a path exists on the device.

        Args:
            path: Absolute path to check.

        Returns:
            True if the path exists.
        """
        ...
