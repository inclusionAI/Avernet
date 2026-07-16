from __future__ import annotations

from engine.community.kernel.frames import EventFrame
from engine.community.plugin_api.openclaw import OpenClawPlugin
from engine.community.local.openclaw import LocalOpenClawPluginImpl


def test_local_openclaw_plugin_satisfies_runtime_protocol_shape():
    plugin = LocalOpenClawPluginImpl()
    assert isinstance(plugin, OpenClawPlugin)


async def test_local_openclaw_plugin_contract_smoke():
    plugin = LocalOpenClawPluginImpl()

    session = await plugin.session_create("s1", label="Local", model="local-model")
    assert session["key"] == "s1"
    assert await plugin.sessions_list() == [session]

    await plugin.upload("/tmp/a.txt", b"hello")
    assert await plugin.read("/tmp/a.txt") == b"hello"
    assert (await plugin.list_dir("/tmp"))["files"]

    events = [event async for event in plugin.chat_stream("s1", "hi")]
    assert events and isinstance(events[0], EventFrame)

    job = await plugin.cron_add({"id": "j1", "disabled": False})
    assert job["id"] == "j1"
    assert await plugin.cron_get("j1") == job

    server = await plugin.create_server({"server_code": "demo", "enabled": True})
    assert server["server_code"] == "demo"
    assert (await plugin.get_server_status("demo"))["status"] == "running"

    shell = await plugin.open_session()
    await shell.write(b"x")
    assert await shell.read() == b"x"
    await shell.close()
    assert shell.closed is True

async def test_local_openclaw_plugin_stateful_ports_and_error_branches():
    plugin = LocalOpenClawPluginImpl()

    # Token pool refcounts and shutdown.
    plugin.pool.register(None)
    plugin.pool.register("tok")
    plugin.pool.register("tok")
    assert plugin.pool.refcount == {"tok": 2}
    await plugin.pool.release("tok")
    assert plugin.pool.refcount == {"tok": 1}
    await plugin.pool.release("tok")
    assert plugin.pool.refcount == {}
    await plugin.pool.shutdown()
    assert plugin.pool.shutdown_called is True

    # Relay/approval/model/node/default-config helpers.
    response = await plugin.forward_request("r1", "demo.method", {"a": 1})
    assert response.ok is True
    assert response.payload["method"] == "demo.method"
    await plugin.forward_raw_frame({"type": "event"})
    assert (await plugin.node_list()) == []
    assert (await plugin.approvals_get("s"))["payload"]["sessionKey"] == "s"
    assert (await plugin.approvals_set("s", "manual"))["payload"]["mode"] == "manual"
    assert (await plugin.models_list())[0]["id"] == "local-model"
    assert (await plugin.providers_list())[0]["id"] == "local"
    assert (await plugin.get_default_config())["path"] == "local://default-config"

    # Cron CRUD and failure branches.
    await plugin.cron_add({"id": "enabled", "disabled": False})
    await plugin.cron_add({"id": "disabled", "disabled": True})
    assert len(await plugin.cron_list(include_disabled=True)) == 2
    assert [j["id"] for j in await plugin.cron_list(include_disabled=False)] == ["enabled"]
    assert (await plugin.cron_status())["total"] == 2
    assert (await plugin.cron_update("enabled", {"name": "n"}))["name"] == "n"
    assert (await plugin.cron_run("enabled", "manual"))["ok"] is True
    assert await plugin.cron_runs("enabled") == []
    assert await plugin.cron_remove("disabled") is True
    assert await plugin.cron_remove("missing") is False
    try:
        await plugin.cron_update("missing", {})
        raise AssertionError("expected missing cron update to fail")
    except RuntimeError as exc:
        assert "cron job not found" in str(exc)
    try:
        await plugin.cron_run("missing", "manual")
        raise AssertionError("expected missing cron run to fail")
    except RuntimeError as exc:
        assert "cron job not found" in str(exc)

    # Session/chat history lifecycle.
    await plugin.session_create("s1", label="one", model="m1")
    await plugin.session_create("s2", label="two", model="m2")
    assert [s["key"] for s in await plugin.sessions_list(offset=1, limit=1)] == ["s2"]
    assert [s["key"] for s in await plugin.sessions_list(session_key="s2", limit=1)] == ["s2"]
    assert await plugin.sessions_list(session_key="missing") == []
    assert [s["key"] for s in await plugin.sessions_list(session_key="   ", limit=1)] == ["s1"]
    await plugin.session_patch_then_get("s1", label="patched")
    assert (await plugin.sessions_list())[0]["label"] == "patched"
    await plugin.session_patch_then_get("new", model="m3")
    assert (await plugin.chat_abort("s1", "run1"))["payload"]["runId"] == "run1"
    events = [event async for event in plugin.chat_stream("s1", "hello")]
    assert events[0].event == "message"
    assert (await plugin.chat_history("s1", limit=1))[0]["content"] == "hello"
    assert (await plugin.session_reset("s1"))["success"] is True
    assert await plugin.chat_history("s1") == []
    await plugin.session_clear("s2")
    assert await plugin.session_delete("s2") is True
    assert await plugin.session_delete("s2") is False

    # File CRUD and failure branches.
    try:
        await plugin.upload("", b"")
        raise AssertionError("expected empty path upload to fail")
    except ValueError:
        pass
    assert await plugin.read("") == b""
    await plugin.upload("/tmp/a.txt", b"a")
    assert (await plugin.upload("/tmp/a.txt", b"aa"))["overwritten"] is True
    await plugin.upload("/tmp/dir/b.txt", b"b")
    assert (await plugin.list_dir("/tmp", recursive=True))["files"]
    assert (await plugin.remove("/tmp/a.txt"))["path_type"] == "file"
    try:
        await plugin.read("/tmp/missing")
        raise AssertionError("expected missing read to fail")
    except FileNotFoundError:
        pass
    try:
        await plugin.remove("/tmp/missing")
        raise AssertionError("expected missing remove to fail")
    except FileNotFoundError:
        pass
    assert await plugin.rmtree("/tmp/dir") == "/tmp/dir"
    try:
        await plugin.rmtree("/tmp/dir")
        raise AssertionError("expected missing rmtree to fail")
    except FileNotFoundError:
        pass

    # MCP CRUD and helper operations.
    assert await plugin.get_server("missing") is None
    created = await plugin.create_server({"server_code": "mcp1", "enabled": False})
    assert created["server_code"] == "mcp1"
    try:
        await plugin.create_server({"server_code": "mcp1"})
        raise AssertionError("expected duplicate server to fail")
    except FileExistsError:
        pass
    assert (await plugin.list_servers())[0]["server_code"] == "mcp1"
    assert (await plugin.get_server("mcp1"))["server_code"] == "mcp1"
    assert (await plugin.get_server_status("mcp1"))["status"] == "stopped"
    assert (await plugin.update_server("mcp1", {"enabled": True}))["server_code"] == "mcp1"
    try:
        await plugin.update_server("missing", {})
        raise AssertionError("expected missing update server to fail")
    except FileNotFoundError:
        pass
    assert (await plugin.call_tool("tool", {"x": 1}))["tool_name"] == "tool"
    assert (await plugin.filter_servers(["mcp1"]))["server_codes"] == ["mcp1"]
    assert await plugin.delete_server("mcp1") is True
    assert await plugin.delete_server("mcp1") is False

    # Skills helpers and web shell.
    assert (await plugin.ensure_center_skills({"items": ["a"]}))["ok"] == ["a"]
    assert (await plugin.sync_symlinks({"symlinks": [1, 2]}))["total"] == 2
    assert (await plugin.sync_bindpaths({"symlinks": [1]}))["total"] == 1
    assert (await plugin.clean_symlinks({"directories": ["a", "b"]}))["directories_scanned"] == 2
    assert plugin.check_token("anything") is True

    shell = await plugin.open_session()
    await shell.resize(40, 120)
    assert (shell.rows, shell.cols) == (40, 120)
    await shell.write(b"abc")
    await shell.write(b"def")
    assert await shell.read() == b"abcdef"
    assert await shell.read() == b""
