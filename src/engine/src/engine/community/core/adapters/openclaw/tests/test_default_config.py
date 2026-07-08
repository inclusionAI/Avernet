"""Unit tests for the OpenClaw default_config ACL adapter.

Drives ``OpenClawDefaultConfigAdapter`` against a fake
``OpenClawDefaultConfigPort`` that returns canned primitive dicts or raises.
Verifies DTO construction and that exceptions propagate unchanged.
"""
from __future__ import annotations

import pytest

from engine.community.core.adapters.openclaw.default_config import OpenClawDefaultConfigAdapter
from engine.community.core.default_config.models import DefaultConfigResult


class _FakeDefaultConfigPort:
    """Fake port that returns a canned result or raises on demand."""

    def __init__(self) -> None:
        self._result: dict | None = None
        self._raise: Exception | None = None

    def will_return(self, path: str, config: dict) -> None:
        self._result = {"path": path, "config": config}

    def will_raise(self, exc: Exception) -> None:
        self._raise = exc

    async def get_default_config(self) -> dict:
        if self._raise:
            raise self._raise
        return self._result  # type: ignore[return-value]


# ── DTO build ──


@pytest.mark.asyncio
async def test_builds_default_config_result_from_primitive_dict():
    port = _FakeDefaultConfigPort()
    port.will_return(
        "/home/admin/agentclaw-daas-scripts/confs/openclaw/openclaw.json",
        {"model": "qwen", "version": 3},
    )
    adapter = OpenClawDefaultConfigAdapter(port)

    result = await adapter.get_default_config()

    assert isinstance(result, DefaultConfigResult)
    assert result.path == "/home/admin/agentclaw-daas-scripts/confs/openclaw/openclaw.json"
    assert result.config == {"model": "qwen", "version": 3}


@pytest.mark.asyncio
async def test_auth_is_accepted_and_ignored():
    """The adapter accepts auth but does not pass it to the port."""
    port = _FakeDefaultConfigPort()
    port.will_return("/some/path.json", {"key": "value"})
    adapter = OpenClawDefaultConfigAdapter(port)

    # Passing auth=None or a sentinel should both work identically.
    result = await adapter.get_default_config(auth=None)
    assert result.path == "/some/path.json"


# ── exception propagation ──


@pytest.mark.asyncio
async def test_file_not_found_propagates():
    port = _FakeDefaultConfigPort()
    port.will_raise(FileNotFoundError("配置文件不存在"))
    adapter = OpenClawDefaultConfigAdapter(port)

    with pytest.raises(FileNotFoundError):
        await adapter.get_default_config()


@pytest.mark.asyncio
async def test_is_a_directory_propagates():
    port = _FakeDefaultConfigPort()
    port.will_raise(IsADirectoryError("配置路径不是文件"))
    adapter = OpenClawDefaultConfigAdapter(port)

    with pytest.raises(IsADirectoryError):
        await adapter.get_default_config()


@pytest.mark.asyncio
async def test_value_error_propagates():
    """JSON parse errors from the port should propagate as ValueError."""
    port = _FakeDefaultConfigPort()
    port.will_raise(ValueError("配置文件 JSON 格式错误"))
    adapter = OpenClawDefaultConfigAdapter(port)

    with pytest.raises(ValueError):
        await adapter.get_default_config()


@pytest.mark.asyncio
async def test_non_object_top_level_value_error_propagates():
    """Non-dict top-level JSON raises ValueError (impl guards, adapter propagates)."""
    port = _FakeDefaultConfigPort()
    port.will_raise(ValueError("配置文件 JSON 顶层必须为 object"))
    adapter = OpenClawDefaultConfigAdapter(port)

    with pytest.raises(ValueError):
        await adapter.get_default_config()
