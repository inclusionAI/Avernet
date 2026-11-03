"""Tests for ArcaErrorClassifier — transient sandbox error classification."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.devices.services.arca_error_classifier import (
    ArcaErrorClassifier,
    _is_arca_unreachable,
)
from agentclaw.community.core.devices.services.device_context import (
    DeviceConnectionUnavailableError,
)


# ── _is_arca_unreachable ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "msg,expected",
    [
        ("connection refused", True),
        ("dial tcp 10.33.78.203:20003: connect: connection refused", True),
        ("error code 11-50201: gateway error", True),
        ("timeout", False),
        ("internal server error", False),
        ("", False),
    ],
)
def test_is_arca_unreachable(msg: str, expected: bool) -> None:
    assert _is_arca_unreachable(Exception(msg)) is expected


# ── list_dir ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_dir_connection_refused_raises_device_unavailable() -> None:
    inner = MagicMock()
    inner.list_dir = AsyncMock(
        side_effect=Exception(
            "Post http://10.33.78.203:20003/api/file/list: "
            "dial tcp 10.33.78.203:20003: connect: connection refused"
        )
    )
    fs = ArcaErrorClassifier(inner, sandbox_id="ARCA-SANDBOX-test")

    with pytest.raises(DeviceConnectionUnavailableError) as exc_info:
        await fs.list_dir("/aidesktop/workspace")

    err = str(exc_info.value)
    assert "ARCA-SANDBOX-test" in err
    assert "list_dir" in err
    assert "/aidesktop/workspace" in err
    assert "connection refused" in err


@pytest.mark.asyncio
async def test_list_dir_11_50201_raises_device_unavailable() -> None:
    inner = MagicMock()
    inner.list_dir = AsyncMock(
        side_effect=Exception("code=11-50201 gateway returned 502")
    )
    fs = ArcaErrorClassifier(inner, sandbox_id="ARCA-SANDBOX-test")

    with pytest.raises(DeviceConnectionUnavailableError) as exc_info:
        await fs.list_dir("/path")

    assert "11-50201" in str(exc_info.value)


@pytest.mark.asyncio
async def test_list_dir_other_error_passes_through() -> None:
    class CustomError(Exception):
        pass

    inner = MagicMock()
    inner.list_dir = AsyncMock(side_effect=CustomError("timeout"))
    fs = ArcaErrorClassifier(inner, sandbox_id="ARCA-SANDBOX-test")

    with pytest.raises(CustomError):
        await fs.list_dir("/path")


@pytest.mark.asyncio
async def test_list_dir_success_returns_result() -> None:
    expected = [{"name": "file.txt", "is_link": False}]
    inner = MagicMock()
    inner.list_dir = AsyncMock(return_value=expected)
    fs = ArcaErrorClassifier(inner, sandbox_id="ARCA-SANDBOX-test")

    result = await fs.list_dir("/path")
    assert result == expected


# ── read_file ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_file_connection_refused_raises_device_unavailable() -> None:
    inner = MagicMock()
    inner.read_file = AsyncMock(
        side_effect=Exception("connection refused")
    )
    fs = ArcaErrorClassifier(inner, sandbox_id="ARCA-SANDBOX-test")

    with pytest.raises(DeviceConnectionUnavailableError) as exc_info:
        await fs.read_file("/path/to/file")

    assert "read_file" in str(exc_info.value)
    assert "/path/to/file" in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_file_other_error_passes_through() -> None:
    inner = MagicMock()
    inner.read_file = AsyncMock(side_effect=OSError("permission denied"))
    fs = ArcaErrorClassifier(inner, sandbox_id="ARCA-SANDBOX-test")

    with pytest.raises(OSError):
        await fs.read_file("/path")


# ── write_file ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_file_connection_refused_raises_device_unavailable() -> None:
    inner = MagicMock()
    inner.write_file = AsyncMock(
        side_effect=Exception("dial tcp: connect: connection refused")
    )
    fs = ArcaErrorClassifier(inner, sandbox_id="test-sandbox")

    with pytest.raises(DeviceConnectionUnavailableError) as exc_info:
        await fs.write_file("/path", b"data")

    assert "write_file" in str(exc_info.value)


# ── delete_file ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_file_connection_refused_raises_device_unavailable() -> None:
    inner = MagicMock()
    inner.delete_file = AsyncMock(
        side_effect=Exception("connection refused")
    )
    fs = ArcaErrorClassifier(inner, sandbox_id="test-sandbox")

    with pytest.raises(DeviceConnectionUnavailableError):
        await fs.delete_file("/path")


# ── delete_tree ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_tree_connection_refused_raises_device_unavailable() -> None:
    inner = MagicMock()
    inner.delete_tree = AsyncMock(
        side_effect=Exception("connection refused")
    )
    fs = ArcaErrorClassifier(inner, sandbox_id="test-sandbox")

    with pytest.raises(DeviceConnectionUnavailableError):
        await fs.delete_tree("/dir")


# ── exists ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exists_connection_refused_raises_device_unavailable() -> None:
    inner = MagicMock()
    inner.exists = AsyncMock(
        side_effect=Exception("11-50201: connection refused")
    )
    fs = ArcaErrorClassifier(inner, sandbox_id="test-sandbox")

    with pytest.raises(DeviceConnectionUnavailableError):
        await fs.exists("/path")


@pytest.mark.asyncio
async def test_exists_success_returns_result() -> None:
    inner = MagicMock()
    inner.exists = AsyncMock(return_value=True)
    fs = ArcaErrorClassifier(inner, sandbox_id="test-sandbox")

    result = await fs.exists("/path")
    assert result is True


# ── sandbox_id fallback ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_sandbox_id_fallback_to_unknown() -> None:
    inner = MagicMock()
    inner.list_dir = AsyncMock(
        side_effect=Exception("connection refused")
    )
    fs = ArcaErrorClassifier(inner, sandbox_id="unknown")

    with pytest.raises(DeviceConnectionUnavailableError) as exc_info:
        await fs.list_dir("/path")

    assert "sandbox_id=unknown" in str(exc_info.value)
