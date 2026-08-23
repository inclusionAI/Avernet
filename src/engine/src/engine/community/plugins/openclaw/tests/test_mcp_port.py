"""Port-impl tests for OpenClawPluginImpl MCP methods (file-IO + subprocess).

Drives list_servers / get_server / create_server / update_server /
delete_server / get_server_status / call_tool / filter_servers directly
on OpenClawPluginImpl against a tmp_path mcporter.json and a monkeypatched
subprocess.run.

Raw dict returns are asserted — DTO builds live in the adapter tests.
Preserves full coverage from legacy engines/openclaw/tests/test_mcp.py.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def cfg_path(tmp_path):
    return tmp_path / "mcporter.json"


@pytest.fixture
def impl(cfg_path, monkeypatch):
    """OpenClawPluginImpl with _mcporter_config_path monkeypatched to tmp_path."""
    inst = OpenClawPluginImpl()
    monkeypatch.setattr(inst, "_mcporter_config_path", lambda: cfg_path)
    return inst


def _seed(cfg_path, servers: dict) -> None:
    cfg_path.write_text(
        json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── list_servers ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_empty_when_no_file(impl):
    out = await impl.list_servers()
    assert out == []


@pytest.mark.asyncio
async def test_list_returns_raw_dicts_sorted_by_code(impl, cfg_path):
    _seed(cfg_path, {
        "git": {"transport": "stdio", "command": "git-mcp", "args": ["serve"]},
        "fs": {"type": "http", "baseUrl": "http://x", "timeoutSeconds": 5, "enabled": True},
    })
    out = await impl.list_servers()
    codes = [e["server_code"] for e in out]
    assert codes == ["fs", "git"]


@pytest.mark.asyncio
async def test_list_preserves_all_raw_fields(impl, cfg_path):
    _seed(cfg_path, {
        "fs": {"type": "http", "baseUrl": "http://x", "enabled": True},
    })
    out = await impl.list_servers()
    assert len(out) == 1
    entry = out[0]
    assert entry["server_code"] == "fs"
    assert entry["type"] == "http"
    assert entry["baseUrl"] == "http://x"
    assert entry["enabled"] is True


# ── get_server ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_server_returns_none_when_missing(impl):
    assert await impl.get_server("nope") is None


@pytest.mark.asyncio
async def test_get_server_returns_raw_dict(impl, cfg_path):
    _seed(cfg_path, {"fs": {"transport": "sse", "url": "http://z", "enabled": False}})
    entry = await impl.get_server("fs")
    assert entry is not None
    assert entry["server_code"] == "fs"
    assert entry["transport"] == "sse"
    assert entry["url"] == "http://z"
    assert entry["enabled"] is False


# ── create_server ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_persists_to_disk(impl, cfg_path):
    entry = {
        "server_code": "new",
        "transport": "sse",
        "url": "http://y",
        "timeout_seconds": 20,
        "enabled": True,
        "description": "test",
        "command": None,
        "args": [],
        "env": {},
        "headers": {},
    }
    result = await impl.create_server(entry)
    assert result["server_code"] == "new"
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "new" in on_disk["mcpServers"]


@pytest.mark.asyncio
async def test_create_rejects_duplicate(impl, cfg_path):
    _seed(cfg_path, {"x": {"transport": "sse", "url": "u"}})
    entry = {"server_code": "x", "transport": "sse", "url": "u"}
    with pytest.raises(FileExistsError):
        await impl.create_server(entry)


@pytest.mark.asyncio
async def test_create_returns_raw_dict_with_server_code(impl, cfg_path):
    entry = {
        "server_code": "myserver",
        "transport": "sse",
        "url": "http://example.com",
        "timeout_seconds": 30,
        "enabled": True,
        "description": "",
        "command": None,
        "args": [],
        "env": {},
        "headers": {},
    }
    result = await impl.create_server(entry)
    assert isinstance(result, dict)
    assert result["server_code"] == "myserver"
    assert result["transport"] == "sse"
    assert result["url"] == "http://example.com"


# ── update_server ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_overwrites_entry_preserves_legacy_keys(impl, cfg_path):
    _seed(cfg_path, {"fs": {"type": "http", "baseUrl": "http://old", "enabled": True}})
    entry = {
        "server_code": "fs",
        "transport": "http",
        "url": "http://new",
        "timeout_seconds": 42,
        "enabled": False,
        "description": "",
        "command": None,
        "args": [],
        "env": {},
        "headers": {},
    }
    result = await impl.update_server("fs", entry)
    # legacy keys preserved on round-trip
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "baseUrl" in on_disk["mcpServers"]["fs"]
    assert on_disk["mcpServers"]["fs"]["baseUrl"] == "http://new"
    assert result["server_code"] == "fs"


@pytest.mark.asyncio
async def test_update_raises_when_missing(impl, cfg_path):
    with pytest.raises(FileNotFoundError):
        await impl.update_server("nope", {"transport": "sse", "url": "u"})


# ── delete_server ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_removes_entry(impl, cfg_path):
    _seed(cfg_path, {"x": {"transport": "sse", "url": "u"}})
    assert await impl.delete_server("x") is True
    # idempotent: second call returns False
    assert await impl.delete_server("x") is False


@pytest.mark.asyncio
async def test_delete_returns_false_when_not_found(impl):
    assert await impl.delete_server("nonexistent") is False


# ── get_server_status ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_server_status_running_when_enabled(impl, cfg_path):
    _seed(cfg_path, {"svc": {"transport": "sse", "url": "u", "enabled": True}})
    status = await impl.get_server_status("svc")
    assert status == {"server_code": "svc", "status": "running"}


@pytest.mark.asyncio
async def test_get_server_status_stopped_when_disabled(impl, cfg_path):
    _seed(cfg_path, {"svc": {"transport": "sse", "url": "u", "enabled": False}})
    status = await impl.get_server_status("svc")
    assert status == {"server_code": "svc", "status": "stopped"}


@pytest.mark.asyncio
async def test_get_server_status_stopped_when_missing(impl):
    status = await impl.get_server_status("no_such_server")
    assert status == {"server_code": "no_such_server", "status": "stopped"}


# ── full lifecycle round-trip ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_crud_lifecycle(impl, cfg_path):
    entry = {
        "server_code": "alpha",
        "transport": "sse",
        "url": "http://a",
        "timeout_seconds": 10,
        "enabled": True,
        "description": "alpha server",
        "command": None,
        "args": [],
        "env": {},
        "headers": {},
    }
    # create
    created = await impl.create_server(entry)
    assert created["server_code"] == "alpha"

    # get
    fetched = await impl.get_server("alpha")
    assert fetched is not None
    assert fetched["url"] == "http://a"

    # status
    status = await impl.get_server_status("alpha")
    assert status["status"] == "running"

    # update
    updated_entry = dict(entry)
    updated_entry["url"] = "http://b"
    updated = await impl.update_server("alpha", updated_entry)
    assert updated["url"] == "http://b"

    # list
    servers = await impl.list_servers()
    codes = [e["server_code"] for e in servers]
    assert "alpha" in codes

    # delete
    assert await impl.delete_server("alpha") is True
    assert await impl.get_server("alpha") is None


# ── call_tool (subprocess) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_tool_builds_correct_argv(impl, monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "tool_output", "")

    # call_tool does a local `import subprocess as _sp; _sp.run(...)`, which
    # resolves to the stdlib subprocess.run — patch that.
    import subprocess as _sp  # noqa: PLC0415
    monkeypatch.setattr(_sp, "run", fake_run)
    result = await impl.call_tool("my_tool", {"key": "val", "num": 42})
    assert captured["cmd"] == ["mcporter", "call", "my_tool", "key=val", "num=42"]
    assert result["tool_name"] == "my_tool"
    assert result["is_error"] is False
    assert result["content"] == [{"type": "text", "text": "tool_output"}]


@pytest.mark.asyncio
async def test_call_tool_uses_30s_timeout(impl, monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "", "")

    import subprocess as _sp  # noqa: PLC0415
    monkeypatch.setattr(_sp, "run", fake_run)
    await impl.call_tool("t", {})
    assert captured["kwargs"]["timeout"] == 30


@pytest.mark.asyncio
async def test_call_tool_nonzero_exit_sets_is_error(impl, monkeypatch):
    import subprocess as _sp  # noqa: PLC0415
    monkeypatch.setattr(
        _sp,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "err msg"),
    )
    result = await impl.call_tool("t", {})
    assert result["is_error"] is True
    assert result["content"][0]["text"] == "err msg"


@pytest.mark.asyncio
async def test_call_tool_no_args(impl, monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    import subprocess as _sp  # noqa: PLC0415
    monkeypatch.setattr(_sp, "run", fake_run)
    await impl.call_tool("bare_tool", {})
    assert captured["cmd"] == ["mcporter", "call", "bare_tool"]


@pytest.mark.asyncio
async def test_call_tool_missing_mcporter_raises_runtime(impl, monkeypatch):
    import subprocess as _sp  # noqa: PLC0415

    def _raise(cmd, **kw):
        raise FileNotFoundError("mcporter not found")

    monkeypatch.setattr(_sp, "run", _raise)
    with pytest.raises(RuntimeError, match="mcporter"):
        await impl.call_tool("t", {})


@pytest.mark.asyncio
async def test_call_tool_timeout_raises_timeout_error(impl, monkeypatch):
    import subprocess as _sp  # noqa: PLC0415

    def _raise(cmd, **kw):
        raise _sp.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(_sp, "run", _raise)
    with pytest.raises(TimeoutError):
        await impl.call_tool("t", {})


# ── filter_servers (subprocess) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filter_servers_passes_csv_to_subprocess(impl, monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "out", "")

    import subprocess as _sp  # noqa: PLC0415
    monkeypatch.setattr(_sp, "run", fake_run)
    result = await impl.filter_servers(["a", "b"], timeout=5)
    assert captured["cmd"] == ["mcporter", "filter-servers", "a,b"]
    assert result["return_code"] == 0
    assert result["stdout"] == "out"
    assert result["server_codes"] == ["a", "b"]


@pytest.mark.asyncio
async def test_filter_servers_timeout_forwarded(impl, monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "", "")

    import subprocess as _sp  # noqa: PLC0415
    monkeypatch.setattr(_sp, "run", fake_run)
    await impl.filter_servers(["x"], timeout=15)
    assert captured["kwargs"]["timeout"] == 15


@pytest.mark.asyncio
async def test_filter_servers_empty_uses_sentinel(impl, monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    import subprocess as _sp  # noqa: PLC0415
    monkeypatch.setattr(_sp, "run", fake_run)
    await impl.filter_servers([])
    assert captured["cmd"][2] == "__EMPTY_FILTER_DISABLE_ALL__"


@pytest.mark.asyncio
async def test_filter_servers_rejects_comma_in_code(impl):
    with pytest.raises(ValueError, match="逗号"):
        await impl.filter_servers(["a,b"])


@pytest.mark.asyncio
async def test_filter_servers_failed_subprocess_raises_runtime(impl, monkeypatch):
    import subprocess as _sp  # noqa: PLC0415
    monkeypatch.setattr(
        _sp,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"),
    )
    with pytest.raises(RuntimeError):
        await impl.filter_servers(["a"])


@pytest.mark.asyncio
async def test_filter_servers_missing_mcporter_raises_runtime(impl, monkeypatch):
    import subprocess as _sp  # noqa: PLC0415

    def _raise(cmd, **kw):
        raise FileNotFoundError("mcporter not found")

    monkeypatch.setattr(_sp, "run", _raise)
    with pytest.raises(RuntimeError, match="mcporter"):
        await impl.filter_servers(["a"])


@pytest.mark.asyncio
async def test_filter_servers_timeout_raises_timeout_error(impl, monkeypatch):
    import subprocess as _sp  # noqa: PLC0415

    def _raise(cmd, **kw):
        raise _sp.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(_sp, "run", _raise)
    with pytest.raises(TimeoutError):
        await impl.filter_servers(["a"])


@pytest.mark.asyncio
async def test_filter_servers_skips_empty_codes(impl, monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    import subprocess as _sp  # noqa: PLC0415
    monkeypatch.setattr(_sp, "run", fake_run)
    result = await impl.filter_servers(["a", "", "b"])
    # empty string stripped → only a,b normalised
    assert captured["cmd"][2] == "a,b"
    assert result["server_codes"] == ["a", "b"]


# ── servers key variant (legacy "servers" key) ────────────────────────────────


@pytest.mark.asyncio
async def test_list_reads_legacy_servers_key(impl, cfg_path):
    """mcporter.json may use "servers" instead of "mcpServers" — both read."""
    cfg_path.write_text(
        json.dumps({"servers": {"alt": {"transport": "sse", "url": "u"}}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    out = await impl.list_servers()
    assert len(out) == 1
    assert out[0]["server_code"] == "alt"
