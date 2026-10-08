"""Engine-neutral file-count Service API, independent of mutable file services."""
from typing import Protocol

from engine.community.core.file.models import CountFilesResult


class FileCountService(Protocol):
    async def count_files(self, path: str) -> CountFilesResult:
        """Count logical regular-file entries under configured runtime roots.

        Relative paths use the canonical engine root, never a guessed cwd.
        Raise FileCountError on failure; never return partial counts or None.
        """
        ...
