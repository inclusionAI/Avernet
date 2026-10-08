"""zip_build — the download-dir archive writer.

Pins the two things the handler contract depends on: archive-tool-compatible
entry metadata (the macOS type-bits lesson from the console router), and the
cleanup rule — a failed build leaves no partial zip on disk.
"""

import os
import stat
import tempfile
import zipfile

import pytest

from agentclaw.community.adapters.http.openapi_v1.resources import zip_build
from agentclaw.community.adapters.http.openapi_v1.resources.zip_build import (
    build_directory_zip,
)


async def _pairs(*items: tuple[str, bytes]):
    for item in items:
        yield item


async def _failing_mid_stream():
    yield ("a.txt", b"aa")
    raise RuntimeError("walk blew up")


@pytest.mark.asyncio
async def test_files_land_under_the_root_name_with_tool_compatible_metadata():
    path = await build_directory_zip(
        _pairs(("a.txt", b"aa"), ("deep/b.txt", b"bb")), "docs"
    )
    try:
        with zipfile.ZipFile(path) as zf:
            assert zf.namelist() == ["docs/", "docs/a.txt", "docs/deep/b.txt"]
            assert zf.read("docs/a.txt") == b"aa"
            assert zf.read("docs/deep/b.txt") == b"bb"
            infos = {i.filename: i for i in zf.infolist()}
            # The Unix type bits GUI archive tools (macOS) require.
            assert infos["docs/"].external_attr >> 16 == stat.S_IFDIR | 0o755
            assert infos["docs/a.txt"].external_attr >> 16 == stat.S_IFREG | 0o644
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_an_empty_walk_is_a_valid_root_only_archive():
    path = await build_directory_zip(_pairs(), "empty")
    try:
        with zipfile.ZipFile(path) as zf:
            assert zf.namelist() == ["empty/"]
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_a_failed_build_leaves_no_partial_zip(monkeypatch):
    created: list[str] = []
    real_named_tempfile = tempfile.NamedTemporaryFile

    def _recording(*args, **kwargs):
        handle = real_named_tempfile(*args, **kwargs)
        created.append(handle.name)
        return handle

    monkeypatch.setattr(zip_build.tempfile, "NamedTemporaryFile", _recording)

    with pytest.raises(RuntimeError):
        await build_directory_zip(_failing_mid_stream(), "docs")

    # The temp file was deleted in place — nothing half-written survives to
    # be served or to leak.
    assert len(created) == 1
    assert not os.path.exists(created[0])
