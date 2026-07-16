"""Rule-25 conformance test for the claude_code local mock plugin.

Drives ``LocalClaudeCodePluginImpl`` (the in-memory test double) through every
domain port to prove the aggregate ``ClaudeCodePlugin`` Protocol seam can be
satisfied without relay/prod dependencies. Mirrors the OpenClaw conformance
test structure (``test_openclaw_local_plugin``) — directly instantiates the
local plugin, no ``world`` fixture.
"""
from __future__ import annotations

from engine.community.kernel.frames import EventFrame
from engine.community.plugin_api.claude_code.plugin import ClaudeCodePlugin
from engine.community.local.claude_code import LocalClaudeCodePluginImpl


def test_local_claude_code_plugin_satisfies_runtime_protocol_shape():
    plugin = LocalClaudeCodePluginImpl()
    assert isinstance(plugin, ClaudeCodePlugin)


async def test_local_claude_code_plugin_contract_smoke():
    plugin = LocalClaudeCodePluginImpl()

    # session: create -> list -> history
    session = await plugin.session_create("s1", label="Local", model="claude")
    assert session["key"] == "s1"
    assert await plugin.sessions_list() == [session]

    # chat: stream yields EventFrames; abort/inject return uniform dicts
    events = [event async for event in plugin.chat_stream("s1", "hi")]
    assert events and all(isinstance(e, EventFrame) for e in events)
    assert events[0].event == "message"
    assert (await plugin.chat_abort("s1", "run1"))["success"] is True
    assert (await plugin.chat_inject("s1", "note"))["success"] is True

    # mcp: create -> list -> status
    server = await plugin.mcp_create_server({"server_code": "demo", "name": "demo"})
    assert server["server_code"] == "demo"
    assert (await plugin.mcp_list_servers())[0]["server_code"] == "demo"
    assert (await plugin.mcp_get_server_status("demo"))["status"] == "running"
    assert await plugin.mcp_call_tool("demo", "tool") == await plugin.mcp_call_tool("demo", "tool")

    # skills: install -> list -> get
    skill = await plugin.skills_install({"id": "sk1", "name": "demo-skill"})
    assert skill["id"] == "sk1"
    assert (await plugin.skills_list())[0]["id"] == "sk1"
    assert (await plugin.skills_get("sk1"))["id"] == "sk1"

    # cron: add -> list -> status
    job = await plugin.cron_add_job({"id": "j1", "name": "daily"})
    assert job["id"] == "j1"
    assert (await plugin.cron_list_jobs())[0]["id"] == "j1"
    assert (await plugin.cron_get_status())["total"] == 1

    # models: list + providers
    assert (await plugin.models_list())[0]["id"] == "claude-sonnet-4-5"
    assert (await plugin.models_list_providers())[0]["id"] == "anthropic"

    # file: upload -> read -> list_dir
    await plugin.file_upload("/tmp/a.txt", b"hello")
    assert (await plugin.file_read("/tmp/a.txt"))["content"] == "hello"
    assert (await plugin.file_list_dir("/tmp"))[0]["name"] == "a.txt"

    # commands: list -> get (None)
    assert await plugin.commands_list() == []
    assert await plugin.commands_get("missing") is None

    # relay: forward_request + forward_raw_frame
    assert (await plugin.relay_forward_request("foo.bar", {"a": 1}))["success"] is True
    assert (await plugin.relay_forward_raw_frame({"type": "event"}))["success"] is True


async def test_local_claude_code_session_key_lookup_is_exact_and_pre_paginated():
    plugin = LocalClaudeCodePluginImpl()
    first = await plugin.session_create("first", label="one")
    target = await plugin.session_create("target", label="two")
    padded = await plugin.session_create(" target ", label="three")
    await plugin.session_create("prefix-target", label="four")
    await plugin.session_create("target-suffix", label="five")

    assert await plugin.sessions_list(session_key="target", offset=0, limit=1) == [target]
    assert await plugin.sessions_list(session_key=" target ") == [padded]
    assert await plugin.sessions_list(session_key="tar") == []
    assert await plugin.sessions_list(session_key="get") == []
    assert await plugin.sessions_list(session_key="missing", offset=0, limit=1) == []
    assert await plugin.sessions_list(session_key="  ", offset=0, limit=1) == [first]


async def test_local_claude_code_agent_lookup_uses_canonical_key_without_agent_id():
    plugin = LocalClaudeCodePluginImpl()
    await plugin.session_create("agent:g2:session:other:user:u1")
    legacy = await plugin.session_create("user:u1:session:legacy:agent:g1")
    target = await plugin.session_create("agent:g1:session:target:user:u1")

    assert await plugin.sessions_list(
        agent_id="g1",
        session_key="agent:g1:session:target:user:u1",
        offset=0,
        limit=1,
    ) == [target]
    assert await plugin.sessions_list(
        agent_id="g1",
        session_key="user:u1:session:legacy:agent:g1",
        offset=0,
        limit=1,
    ) == [legacy]


async def test_local_claude_code_agent_lookup_skips_malformed_session_keys():
    plugin = LocalClaudeCodePluginImpl()
    plugin._sessions = {
        "non-string": {"key": None},
        "canonical": {"key": "agent:g1:session:missing-user"},
        "legacy": {"key": "user:u1:session:missing-agent"},
        "unknown": {"key": "unknown:g1"},
    }

    assert await plugin.sessions_list(agent_id="g1") == []

    explicit = {
        "key": "agent:g2:session:explicit:user:u1",
        "agentId": "g1",
    }
    plugin._sessions["explicit"] = explicit
    assert await plugin.sessions_list(
        agent_id="g1",
        session_key="agent:g2:session:explicit:user:u1",
        offset=0,
        limit=1,
    ) == [explicit]


async def test_local_claude_code_session_key_diagnostics_do_not_log_the_key(caplog):
    plugin = LocalClaudeCodePluginImpl()
    await plugin.session_create("private-session-key")

    with caplog.at_level("INFO", logger="local-claude-code-plugin"):
        await plugin.sessions_list(session_key="private-session-key")

    assert "has_session_key=True" in caplog.text
    assert "private-session-key" not in caplog.text


async def test_local_claude_code_plugin_stateful_ports_and_error_branches():
    plugin = LocalClaudeCodePluginImpl()

    # session lifecycle: delete then list empty; history reset/clear.
    await plugin.session_create("s1", label="one")
    await plugin.chat_inject("s1", "first")
    assert (await plugin.session_get_history("s1"))[0]["content"] == "first"
    assert (await plugin.session_reset("s1"))["success"] is True
    assert await plugin.session_get_history("s1") == []
    await plugin.chat_inject("s1", "again")
    assert (await plugin.session_clear("s1"))["success"] is True
    assert await plugin.session_get_history("s1") == []
    assert await plugin.session_delete("s1") is True
    assert await plugin.session_delete("s1") is False
    assert await plugin.sessions_list() == []
    assert await plugin.session_get_history("missing") == []

    # mcp CRUD + filter.
    assert await plugin.mcp_get_server("missing") is None
    created = await plugin.mcp_create_server({"server_code": "mcp1", "name": "n1"})
    assert created["server_code"] == "mcp1"
    updated = await plugin.mcp_update_server("mcp1", {"name": "n2"})
    assert updated["name"] == "n2"
    assert (await plugin.mcp_get_server("mcp1"))["name"] == "n2"
    assert (await plugin.mcp_start_server("mcp1"))["payload"]["status"] == "running"
    assert (await plugin.mcp_stop_server("mcp1"))["payload"]["status"] == "stopped"
    assert (await plugin.mcp_restart_server("mcp1"))["payload"]["status"] == "running"
    assert (await plugin.mcp_list_tools("mcp1")) == []
    assert (await plugin.mcp_list_resources("mcp1")) == []
    assert (await plugin.mcp_list_prompts("mcp1")) == []
    assert (await plugin.mcp_read_resource("mcp1", "uri:1"))["success"] is True
    assert (await plugin.mcp_get_prompt("mcp1", "p"))["success"] is True
    assert len(await plugin.mcp_filter_servers("mcp1")) == 1
    assert len(await plugin.mcp_filter_servers()) == 1
    assert await plugin.mcp_delete_server("mcp1") is True
    assert await plugin.mcp_delete_server("mcp1") is False
    assert await plugin.mcp_filter_servers() == []

    # skills CRUD + enable/disable + execute/validate/discover.
    assert await plugin.skills_get("missing") is None
    sk = await plugin.skills_install({"id": "sk1", "name": "demo"})
    assert sk["id"] == "sk1"
    assert await plugin.skills_enable("sk1") is True
    assert (await plugin.skills_get("sk1"))["enabled"] is True
    assert await plugin.skills_disable("sk1") is True
    assert (await plugin.skills_get("sk1"))["enabled"] is False
    assert await plugin.skills_enable("missing") is False
    assert (await plugin.skills_update("sk1", {"version": "2"}))["version"] == "2"
    assert (await plugin.skills_execute("sk1", {"x": 1}))["success"] is True
    assert (await plugin.skills_validate({"id": "sk1"}))["valid"] is True
    assert (await plugin.skills_discover("registry"))[0]["source"] == "registry"
    assert (await plugin.skills_sync_symlinks())["ok"] is True
    assert (await plugin.skills_sync_bindpaths())["ok"] is True
    assert (await plugin.skills_clean_symlinks())["ok"] is True
    assert (await plugin.skills_ensure_center())["ok"] is True
    assert await plugin.skills_uninstall("sk1") is True
    assert await plugin.skills_uninstall("sk1") is False
    assert await plugin.skills_get("sk1") is None

    # cron CRUD + run + runs + running.
    assert await plugin.cron_get_job("missing") is None
    job = await plugin.cron_add_job({"id": "j1", "name": "daily"})
    assert job["id"] == "j1"
    assert (await plugin.cron_get_job("j1"))["name"] == "daily"
    assert (await plugin.cron_update_job("j1", {"name": "hourly"}))["name"] == "hourly"
    assert (await plugin.cron_run_job("j1"))["success"] is True
    assert await plugin.cron_get_runs("j1") == []
    assert await plugin.cron_get_running_jobs() == []
    assert (await plugin.cron_get_status())["total"] == 1
    assert await plugin.cron_remove_job("j1") is True
    assert await plugin.cron_remove_job("j1") is False
    assert await plugin.cron_list_jobs() == []

    # file CRUD + rmtree + list_dir.
    await plugin.file_upload("/tmp/a.txt", b"a")
    await plugin.file_upload("/tmp/dir/b.txt", b"b")
    assert (await plugin.file_read("/tmp/a.txt"))["content"] == "a"
    assert len(await plugin.file_list_dir("/tmp")) == 2
    assert await plugin.file_remove("/tmp/a.txt") is True
    assert await plugin.file_remove("/tmp/a.txt") is False
    assert await plugin.file_rmtree("/tmp/dir") is True
    assert await plugin.file_rmtree("/tmp/dir") is False
    assert (await plugin.file_list_dir("/tmp")) == []

    # commands + relay never raise and return uniform shapes.
    assert await plugin.commands_list(scope="builtin") == []
    assert await plugin.commands_get("anything") is None
    fwd = await plugin.relay_forward_request("m", {"p": 1}, request_id="r1")
    assert fwd["success"] is True and fwd["payload"]["method"] == "m"
    raw = await plugin.relay_forward_raw_frame({"type": "event", "event": "x"})
    assert raw["success"] is True

    # chat resolve_* branches.
    assert (await plugin.resolve_exec_approval("s", "r1", "allow"))["payload"]["decision"] == "allow"
    assert (await plugin.resolve_interaction("s", "r1", "yes"))["payload"]["response"] == "yes"
    assert (await plugin.resolve_mode_transition("s", "r1", "accept"))["payload"]["decision"] == "accept"
