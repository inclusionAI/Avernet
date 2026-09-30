"""Zip construction for the directory-download endpoint.

Transport-layer packaging: ``ResourceFileService.iter_directory_files`` yields
``(name, content)`` pairs and this module turns them into an on-disk archive
the handler answers with. It sits beside the router the way ``schemas.py``
does — the router's job is the contract, not archive mechanics.

The archive is built **on disk** (a tempfile the response's background task
deletes), never in memory: a 500 MB cap in memory is a different beast from
500 MB on disk. Per-file bytes still arrive whole — that is the device
transports' own shape (Arca reads whole files), not something this layer can
stream around.
"""

from __future__ import annotations

import os
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import AsyncIterator


def _zip_file_entry(name: str) -> zipfile.ZipInfo:
    """A ``ZipInfo`` for a FILE with archive-tool-compatible metadata.

    ``zipfile.ZipFile.writestr(str, bytes)`` leaves ``external_attr``'s Unix
    type bits at ``0`` (no ``S_IFREG``), so the zip carries no machine-readable
    "this is a regular file" tag. macOS Archive Utility reads those bits and,
    finding none, refuses the entry — even though ``unzip``/``ditto`` (and
    Windows Explorer, which keys off the trailing ``/`` instead) extract fine.
    Mirrors the console's ``_zip_file_entry``
    (``adapters/http/resources/file_search_download_router.py``).
    """
    zi = zipfile.ZipInfo(name)
    zi.external_attr = (stat.S_IFREG | 0o644) << 16
    return zi


def _zip_dir_entry(name: str) -> zipfile.ZipInfo:
    """A trailing-``/`` directory ``ZipInfo`` so GUI archive tools see the
    root folder up front. ``writestr`` with bytes alone emits no directory
    entries."""
    zi = zipfile.ZipInfo(name if name.endswith("/") else name + "/")
    zi.external_attr = (stat.S_IFDIR | 0o755) << 16
    return zi


async def build_directory_zip(
    files: AsyncIterator[tuple[str, bytes]], root_name: str
) -> Path:
    """Write every yielded ``(name, content)`` into a fresh temp zip.

    ``root_name`` becomes the archive's single top-level directory. Any
    failure — a walk error from the service, a disk error here — deletes the
    partial archive and re-raises: a half-written zip is never an answer. The
    returned path is the caller's to clean up (the handler hands it to
    ``FileResponse``'s background task).
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    try:
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.writestr(_zip_dir_entry(root_name), b"")
            async for name, content in files:
                zf.writestr(_zip_file_entry(f"{root_name}/{name}"), content)
    except BaseException:
        os.unlink(tmp.name)
        raise
    return Path(tmp.name)
