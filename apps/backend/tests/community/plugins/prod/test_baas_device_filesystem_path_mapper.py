"""BaasDeviceFileSystem applies its injected ``path_mapper`` before transport.

Mirrors the Arca mapper behavior: the identity flow injects a mapper turning
``identity/<file>`` into the per-engine address; skills/resources construct the
plugin without a mapper, so resolved host paths pass through unchanged. The
mapper lives on the base class, so ``DesktopBaasDeviceFileSystem`` inherits it.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.baas_device_filesystem import (
    BaasDeviceFileSystem,
    DesktopBaasDeviceFileSystem,
)


def _mapper(path: str) -> str:
    if path.startswith("identity/"):
        return "/aidesktop/aidesktop_pre/bolt_data/staff_x/default/openclaw/workspace/" + path.split("/", 1)[1]
    return path


def _transport_returning(content: bytes) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.raise_for_status = MagicMock()
    transport = MagicMock()
    transport.post = MagicMock(return_value=resp)
    transport.post_multipart = MagicMock(return_value=resp)
    return transport


@pytest.mark.asyncio
async def test_read_maps_logical_path_before_post():
    transport = _transport_returning(b"# c")
    fs = BaasDeviceFileSystem(transport=transport, conn_info={}, path_mapper=_mapper)

    await fs.read_file("identity/IDENTITY.md")

    transport.post.assert_called_once_with(
        "/api/file/read",
        json={
            "file_path": "/aidesktop/aidesktop_pre/bolt_data/staff_x/default/openclaw/workspace/IDENTITY.md"
        },
    )


@pytest.mark.asyncio
async def test_write_maps_logical_path_before_multipart():
    transport = _transport_returning(b"")
    fs = BaasDeviceFileSystem(transport=transport, conn_info={}, path_mapper=_mapper)

    await fs.write_file("identity/IDENTITY.md", b"# c")

    mapped = "/aidesktop/aidesktop_pre/bolt_data/staff_x/default/openclaw/workspace/IDENTITY.md"
    transport.post_multipart.assert_called_once_with(
        "/api/file/upload",
        files={"file": (mapped, b"# c")},
        data={"target_path": mapped},
    )


@pytest.mark.asyncio
async def test_no_mapper_passes_host_path_through():
    transport = _transport_returning(b"x")
    fs = BaasDeviceFileSystem(transport=transport, conn_info={}, path_mapper=lambda p: p)
    host = "/aidesktop/aidesktop_pre/bolt_data/staff_x/bot/openclaw/workspace/r.txt"

    await fs.read_file(host)

    transport.post.assert_called_once_with("/api/file/read", json={"file_path": host})


@pytest.mark.asyncio
async def test_exists_maps_once_via_delegation():
    """exists() delegates to read_file/list_dir — the mapper applies once, not twice."""
    transport = _transport_returning(b"# c")
    fs = BaasDeviceFileSystem(transport=transport, conn_info={}, path_mapper=_mapper)

    assert await fs.exists("identity/IDENTITY.md") is True

    mapped = "/aidesktop/aidesktop_pre/bolt_data/staff_x/default/openclaw/workspace/IDENTITY.md"
    # read_file short-circuits exists() → single /api/file/read with the once-mapped path
    transport.post.assert_called_once_with("/api/file/read", json={"file_path": mapped})


@pytest.mark.asyncio
async def test_desktop_subclass_inherits_mapper():
    transport = _transport_returning(b"x")
    fs = DesktopBaasDeviceFileSystem(
        transport=transport, conn_info={}, path_mapper=_mapper
    )

    await fs.read_file("identity/RULES.md")

    transport.post.assert_called_once_with(
        "/api/file/read",
        json={
            "file_path": "/aidesktop/aidesktop_pre/bolt_data/staff_x/default/openclaw/workspace/RULES.md"
        },
    )
