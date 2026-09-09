"""Tests for SkillParameterService (async, DeviceFileSystemPlugin-based)."""

import json
from unittest.mock import AsyncMock

import pytest

from agentclaw.community.core.skill_center.services.skill_parameter_service import (
    DEFAULT_PARAMETERS_PATH,
    SkillParameterService,
)
from agentclaw.community.core.skill_center.errors import LocalSkillStorageError


@pytest.fixture
def mock_device_fs():
    fs = AsyncMock()
    fs.read_file = AsyncMock(return_value=None)
    fs.write_file = AsyncMock(return_value=None)
    return fs


@pytest.fixture
def svc(mock_device_fs):
    return SkillParameterService(device_fs=mock_device_fs)


# ---------- async_load ----------


@pytest.mark.asyncio
async def test_async_load_returns_empty_when_read_file_returns_none(
    svc, mock_device_fs
):
    """read_file returns None → _data initialises with empty parameters."""
    mock_device_fs.read_file.return_value = None
    await svc.async_load()
    assert svc._data == {"parameters": {}}
    mock_device_fs.read_file.assert_awaited_once_with(
        DEFAULT_PARAMETERS_PATH,
        preserve_read_errors=True,
    )


@pytest.mark.asyncio
async def test_async_load_parses_valid_json(svc, mock_device_fs):
    """read_file returns valid JSON → _data is populated correctly."""
    payload = {
        "parameters": {"my_skill": {"key": "val"}},
        "updated_at": "2025-01-01T00:00:00",
    }
    mock_device_fs.read_file.return_value = json.dumps(payload).encode("utf-8")
    await svc.async_load()
    assert svc._data == payload
    assert svc.get_skill_parameters("my_skill") == {"key": "val"}


@pytest.mark.asyncio
async def test_async_load_rejects_invalid_json_without_treating_it_as_empty(
    svc, mock_device_fs
):
    mock_device_fs.read_file.return_value = b"NOT JSON{{"
    with pytest.raises(LocalSkillStorageError):
        await svc.async_load()
    assert svc._data == {}
    mock_device_fs.write_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_load_rejects_invalid_structure_and_read_failures(
    svc, mock_device_fs
):
    mock_device_fs.read_file.return_value = b'{"parameters": []}'
    with pytest.raises(LocalSkillStorageError):
        await svc.async_load()
    mock_device_fs.write_file.assert_not_awaited()

    mock_device_fs.read_file.side_effect = TimeoutError("device timed out")
    with pytest.raises(LocalSkillStorageError):
        await svc.async_load()
    mock_device_fs.write_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_load_custom_path():
    """Constructor accepts a custom file_path."""
    fs = AsyncMock()
    fs.read_file = AsyncMock(return_value=None)
    custom = "/tmp/custom/params.json"
    svc = SkillParameterService(device_fs=fs, file_path=custom)
    await svc.async_load()
    fs.read_file.assert_awaited_once_with(custom, preserve_read_errors=True)


# ---------- save_skill_parameters ----------


@pytest.mark.asyncio
async def test_save_skill_parameters_writes_json(svc, mock_device_fs):
    """save_skill_parameters stores in _data and calls write_file."""
    await svc.async_load()
    await svc.save_skill_parameters("my_skill", {"api_key": "abc123"})

    mock_device_fs.write_file.assert_awaited_once()
    call_args = mock_device_fs.write_file.call_args
    path_arg, content_arg = call_args[0]
    assert path_arg == DEFAULT_PARAMETERS_PATH
    written = json.loads(content_arg.decode("utf-8"))
    assert written["parameters"]["my_skill"] == {"api_key": "abc123"}
    assert "updated_at" in written


@pytest.mark.asyncio
async def test_save_replaces_only_one_skill_and_preserves_metadata(svc, mock_device_fs):
    payload = {
        "format_version": 1,
        "parameters": {"a": {"old": True}, "b": {"kept": 2}},
        "updated_at": "2025-01-01T00:00:00",
    }
    mock_device_fs.read_file.return_value = json.dumps(payload).encode()
    await svc.async_load()

    assert await svc.save_skill_parameters("a", {"new": False}) is True

    written = json.loads(mock_device_fs.write_file.await_args.args[1])
    assert written["format_version"] == 1
    assert written["parameters"] == {"a": {"new": False}, "b": {"kept": 2}}


# ---------- delete_skill_parameters ----------


@pytest.mark.asyncio
async def test_delete_skill_parameters_removes_key(svc, mock_device_fs):
    """delete removes the skill key and persists."""
    payload = {"parameters": {"a": {"x": 1}, "b": {"y": 2}}}
    mock_device_fs.read_file.return_value = json.dumps(payload).encode()
    await svc.async_load()

    await svc.delete_skill_parameters("a")

    assert "a" not in svc._data["parameters"]
    assert "b" in svc._data["parameters"]
    mock_device_fs.write_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_nonexistent_skill_is_noop(svc, mock_device_fs):
    """Deleting a skill that doesn't exist should still succeed and persist."""
    await svc.async_load()
    await svc.delete_skill_parameters("nonexistent")
    # write_file is still called (current behaviour)
    mock_device_fs.write_file.assert_awaited_once()


# ---------- sync readers ----------


def test_get_skill_parameters_empty(svc):
    """Before load, returns empty dict."""
    assert svc.get_skill_parameters("anything") == {}


def test_get_all_parameters_empty(svc):
    assert svc.get_all_parameters() == {}


# ---------- check_parameters_required ----------


@pytest.mark.asyncio
async def test_check_parameters_required_finds_missing(svc, mock_device_fs):
    payload = {"parameters": {"my_skill": {"token": "ok"}}}
    mock_device_fs.read_file.return_value = json.dumps(payload).encode()
    await svc.async_load()

    schema = [
        {"name": "token", "required": True},
        {"name": "secret", "required": True},
        {"name": "optional_field", "required": False},
    ]
    has_missing, missing = svc.check_parameters_required("my_skill", schema)
    assert has_missing is True
    assert len(missing) == 1
    assert missing[0]["name"] == "secret"


def test_check_parameters_required_empty_schema(svc):
    has_missing, missing = svc.check_parameters_required("x", [])
    assert has_missing is False
    assert missing == []


@pytest.mark.asyncio
async def test_check_parameters_required_all_present(svc, mock_device_fs):
    payload = {"parameters": {"s": {"a": "1", "b": "2"}}}
    mock_device_fs.read_file.return_value = json.dumps(payload).encode()
    await svc.async_load()

    schema = [{"name": "a", "required": True}, {"name": "b", "required": True}]
    has_missing, missing = svc.check_parameters_required("s", schema)
    assert has_missing is False
    assert missing == []
