"""Tests for agentclaw.community.core.channel.json_config_utils."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentclaw.community.core.channel import json_config_utils as jcu
from agentclaw.community.core.channel.json_config_utils import JsonConfigFile, JsonConfigUtils


@pytest.fixture(autouse=True)
def _isolate_local_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep _rewrite_path_for_local's output inside the per-test tmp dir.

    Without this, any test that passes device_fs=None drops its files into
    ~/.openclaw/, which leaks state between runs and misses the intended path.
    """
    monkeypatch.setattr(jcu, "_get_local_base_dir", lambda: tmp_path)


def test_is_local_mode_none():
    assert jcu._is_local_mode(None) is True


def test_is_local_mode_with_local_device_fs():
    class LocalDeviceFileSystem:
        pass
    assert jcu._is_local_mode(LocalDeviceFileSystem()) is True


def test_is_local_mode_with_arca():
    class ArcaDeviceFileSystem:
        pass
    assert jcu._is_local_mode(ArcaDeviceFileSystem()) is False


def test_rewrite_path_local_mode(tmp_path: Path):
    new = jcu._rewrite_path_for_local("/orig/config.json", None)
    assert new == tmp_path / "config.json"


def test_rewrite_path_not_local():
    class ArcaDeviceFileSystem:
        pass
    result = jcu._rewrite_path_for_local("/orig/config.json", ArcaDeviceFileSystem())
    assert result == Path("/orig/config.json")


class TestJsonConfigFileInMemory:
    def _make(self, data: dict | None = None) -> JsonConfigFile:
        return JsonConfigFile(Path("dummy.json"), data or {})

    def test_parse_key_path(self):
        assert JsonConfigFile._parse_key_path("a.b.c") == ["a", "b", "c"]

    def test_get_simple(self):
        cfg = self._make({"a": 1})
        assert cfg.get("a") == 1

    def test_get_nested(self):
        cfg = self._make({"a": {"b": {"c": "v"}}})
        assert cfg.get("a.b.c") == "v"

    def test_get_missing_returns_default(self):
        cfg = self._make({})
        assert cfg.get("missing", default="D") == "D"

    def test_set_and_get(self):
        cfg = self._make({})
        cfg.set("a.b", 42)
        assert cfg.get("a.b") == 42
        assert cfg._modified is True

    def test_set_overwrites(self):
        cfg = self._make({"a": {"b": 1}})
        cfg.set("a.b", 2)
        assert cfg.get("a.b") == 2

    def test_delete_existing(self):
        cfg = self._make({"a": {"b": 1, "c": 2}})
        assert cfg.delete("a.b") is True
        assert cfg.get("a.b") is None
        assert cfg.get("a.c") == 2

    def test_delete_missing(self):
        cfg = self._make({"a": {}})
        assert cfg.delete("a.nope") is False
        assert cfg.delete("missing.deep") is False

    def test_exists(self):
        cfg = self._make({"a": {"b": 1}})
        assert cfg.exists("a.b") is True
        assert cfg.exists("a.x") is False

    def test_to_dict(self):
        cfg = self._make({"a": 1})
        d = cfg.to_dict()
        assert d == {"a": 1}
        # Shallow copy
        d["b"] = 2
        assert "b" not in cfg._data

    def test_dumps(self):
        cfg = self._make({"a": 1})
        s = cfg.dumps()
        assert json.loads(s) == {"a": 1}


@pytest.mark.asyncio
async def test_load_local_fs(tmp_path: Path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"key": "value"}))
    cfg = await JsonConfigFile.load(p)
    assert cfg.get("key") == "value"


@pytest.mark.asyncio
async def test_load_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        await JsonConfigFile.load(tmp_path / "nope.json")


@pytest.mark.asyncio
async def test_save_writes_when_modified(tmp_path: Path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"a": 1}))
    cfg = await JsonConfigFile.load(p)
    cfg.set("a", 2)
    await cfg.save()
    assert json.loads(p.read_text()) == {"a": 2}


@pytest.mark.asyncio
async def test_save_noop_when_unmodified(tmp_path: Path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"a": 1}))
    cfg = await JsonConfigFile.load(p)
    await cfg.save()  # no modification
    assert json.loads(p.read_text()) == {"a": 1}


@pytest.mark.asyncio
async def test_utils_load(tmp_path: Path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"a": {"b": "c"}}))
    data = await JsonConfigUtils.load(p)
    assert data == {"a": {"b": "c"}}


@pytest.mark.asyncio
async def test_utils_load_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        await JsonConfigUtils.load(tmp_path / "nope.json")


def test_utils_dump(tmp_path: Path):
    p = tmp_path / "cfg.json"
    JsonConfigUtils.dump(p, {"k": "v"})
    assert json.loads(p.read_text()) == {"k": "v"}


@pytest.mark.asyncio
async def test_utils_get(tmp_path: Path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"a": {"b": "value"}}))
    assert await JsonConfigUtils.get(p, "a.b") == "value"
    assert await JsonConfigUtils.get(p, "missing", default="D") == "D"


@pytest.mark.asyncio
async def test_utils_set(tmp_path: Path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"a": {}}))
    await JsonConfigUtils.set(p, "a.b", 2)
    assert json.loads(p.read_text())["a"]["b"] == 2


@pytest.mark.asyncio
async def test_utils_delete_existing(tmp_path: Path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"a": {"b": 1}}))
    assert await JsonConfigUtils.delete(p, "a.b") is True


@pytest.mark.asyncio
async def test_utils_delete_missing(tmp_path: Path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"a": 1}))
    assert await JsonConfigUtils.delete(p, "nope") is False


@pytest.mark.asyncio
async def test_utils_exists(tmp_path: Path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"a": 1}))
    assert await JsonConfigUtils.exists(p, "a") is True
    assert await JsonConfigUtils.exists(p, "b") is False


@pytest.mark.asyncio
async def test_utils_exists_file_missing(tmp_path: Path):
    assert await JsonConfigUtils.exists(tmp_path / "nope.json", "a") is False


@pytest.mark.asyncio
async def test_utils_batch_set(tmp_path: Path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"a": {}}))
    await JsonConfigUtils.batch_set(p, {"a.b": 1, "c": "x"})
    data = json.loads(p.read_text())
    assert data["a"]["b"] == 1
    assert data["c"] == "x"
