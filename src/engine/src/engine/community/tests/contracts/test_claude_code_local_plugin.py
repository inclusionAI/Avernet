"""Rule-25 conformance test for the claude_code local mock plugin.

Drives ``LocalClaudeCodePluginImpl`` (the in-memory test double) through every
domain port to prove the aggregate ``ClaudeCodePlugin`` Protocol seam can be
satisfied without relay/prod dependencies. Mirrors the OpenClaw conformance
test structure (``test_openclaw_local_plugin``) — directly instantiates the
local plugin, no ``world`` fixture.
"""
from __future__ import annotations

import pytest

from engine.community.core.adapters.claude_code.session import ClaudeCodeSessionAdapter
from engine.community.core.session.models import SessionListRequest
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
    assert (await plugin.file_read("/tmp/a.txt"))["content"] == b"hello"
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


async def test_local_claude_code_source_filter_via_adapter_before_pagination():
    plugin = LocalClaudeCodePluginImpl()
    await plugin.session_create("session:other:user:u2")
    current = await plugin.session_create("session:current:user:u1")
    userless = await plugin.session_create("session:legacy")
    adapter = ClaudeCodeSessionAdapter(plugin)

    sessions = await adapter.list(SessionListRequest(
        source="all_but_others", user_id="u1", limit=10,
    ))
    assert [session.key for session in sessions] == [current["key"], userless["key"]]

    padded = await adapter.list(SessionListRequest(
        source="all_but_others", user_id=" u1 ", limit=10,
    ))
    assert [session.key for session in padded] == [current["key"], userless["key"]]

    blank = await adapter.list(SessionListRequest(
        source="all_but_others", user_id="   ", limit=10,
    ))
    assert blank == []

    page = await adapter.list(SessionListRequest(
        source="all_but_others", user_id="u1", offset=1, limit=1,
    ))
    assert [session.key for session in page] == [userless["key"]]


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
    assert (await plugin.skills_sync_symlinks({}))["ok"] is True
    assert (await plugin.skills_sync_bindpaths({}))["ok"] is True
    assert (await plugin.skills_clean_symlinks({}))["ok"] is True
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
    assert (await plugin.file_read("/tmp/a.txt"))["content"] == b"a"
    assert len(await plugin.file_list_dir("/tmp")) == 2
    assert (await plugin.file_remove("/tmp/a.txt"))["path_type"] == "file"
    with pytest.raises(FileNotFoundError):
        await plugin.file_remove("/tmp/a.txt")
    assert await plugin.file_rmtree("/tmp/dir") is True
    with pytest.raises(FileNotFoundError):
        await plugin.file_rmtree("/tmp/dir")
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


@pytest.mark.parametrize("implementation", ["memory", "filesystem"])
async def test_file_adapter_distinguishes_missing_and_empty_directories(tmp_path, implementation):
    from engine.community.core.adapters.claude_code.file import ClaudeCodeFileAdapter
    from engine.community.plugins.skills_pool.center_content import MountedCenterContentAdapter
    from engine.community.plugins.claude_code.plugin_impl import ClaudeCodePluginImpl
    port = LocalClaudeCodePluginImpl() if implementation == "memory" else ClaudeCodePluginImpl(center_content_adapter=MountedCenterContentAdapter(), file_roots=(tmp_path,))
    adapter = ClaudeCodeFileAdapter(port)
    project = str(tmp_path / 'project')
    with pytest.raises(FileNotFoundError):
        await adapter.list_dir(project)
    await adapter.upload(project + '/last.bin', b'\xff\x00')
    assert await adapter.read(project + '/last.bin') == b'\xff\x00'
    await adapter.remove(project + '/last.bin')
    assert (await adapter.list_dir(project)).files == []
    await adapter.rmtree(project)
    with pytest.raises(FileNotFoundError):
        await adapter.list_dir(project)


async def test_adapter_local_skill_activation_contract():
    from engine.community.core.adapters.claude_code.skills import ClaudeCodeSkillsAdapter
    from engine.community.core.skills.models import SyncBindPathsRequest, SymlinkItem, CleanSymlinksRequest
    plugin = LocalClaudeCodePluginImpl()
    adapter = ClaudeCodeSkillsAdapter(plugin)
    source, target = "/source/retro", "/active/retro"
    await plugin.file_upload(source + "/SKILL.md", b"retro")
    request = SyncBindPathsRequest(symlinks=[SymlinkItem(source=source, target=target)])
    assert (await adapter.sync_bindpaths(request)).created == [target]
    assert plugin._skill_links == {target: source}
    assert (await adapter.sync_bindpaths(request)).kept == [target]
    with pytest.raises(RuntimeError):
        await adapter.sync_bindpaths(SyncBindPathsRequest(symlinks=[SymlinkItem(source="/absent", target=target)]))
    assert plugin._skill_links == {target: source}
    assert (await adapter.clean_symlinks(CleanSymlinksRequest(directories=["/active"]))).removed == [target]
    assert (await plugin.file_read(source + "/SKILL.md"))["content"] == b"retro"


async def test_local_activation_replacement_and_relative_cleanup():
    plugin = LocalClaudeCodePluginImpl()
    base = "/home/admin/.claude/skills"
    for name in ("one", "two"):
        await plugin.file_upload(f"{base}/sources/{name}/SKILL.md", b"skill")
    def relative(source, target):
        return {"symlinks": [{"source": f"sources/{source}", "target": target}]}
    assert (await plugin.skills_sync_symlinks(relative("one", "nested/active")))["created"] == ["nested/active"]
    assert (await plugin.skills_sync_symlinks(relative("two", "nested/active")))["updated"] == ["nested/active"]
    assert (await plugin.skills_sync_symlinks({"symlinks": []}))["removed"] == ["nested/active"]
    for name in ("old", "new"):
        await plugin.skills_sync_bindpaths({"symlinks": [{"source": f"{base}/sources/one", "target": f"{base}/{name}"}], "clean_target_dir": False})
    result = await plugin.skills_sync_bindpaths({"symlinks": [{"source": f"{base}/sources/one", "target": f"{base}/new"}]})
    assert result["kept"] == [f"{base}/new"]
    assert result["removed"] == [f"{base}/old"]


@pytest.mark.parametrize("method,params,error", [
    ("skills_sync_symlinks", {"symlinks": [{"source": "../escape", "target": "x"}]}, ValueError),
    ("skills_sync_bindpaths", {"symlinks": [{"source": "relative", "target": "/x"}]}, ValueError),
    ("skills_sync_bindpaths", {"symlinks": [{"source": "/source", "target": "/source"}]}, ValueError),
    ("skills_sync_bindpaths", {"symlinks": [{"source": "/source", "target": "/source/SKILL.md"}]}, RuntimeError),
    ("skills_clean_symlinks", {"directories": ["relative"]}, ValueError),
    ("skills_clean_symlinks", {"directories": ["/source/SKILL.md"]}, ValueError),
])
async def test_local_activation_rejects_invalid_payload(method, params, error):
    plugin = LocalClaudeCodePluginImpl()
    await plugin.file_upload("/source/SKILL.md", b"skill")
    with pytest.raises(error):
        await getattr(plugin, method)(params)
    assert plugin._skill_links == {}


async def test_local_activation_rejects_nested_batch_targets():
    plugin = LocalClaudeCodePluginImpl()
    await plugin.file_upload("/source/SKILL.md", b"skill")
    with pytest.raises(ValueError):
        await plugin.skills_sync_bindpaths({"symlinks": [
            {"source": "/source", "target": "/active/retro"},
            {"source": "/source", "target": "/active/retro/nested"},
        ]})
    assert plugin._skill_links == {}


async def test_local_activation_rejects_parent_link_from_previous_request():
    plugin = LocalClaudeCodePluginImpl()
    await plugin.file_upload("/source/SKILL.md", b"skill")
    await plugin.skills_sync_bindpaths({"symlinks": [{"source": "/source", "target": "/active/retro"}]})
    with pytest.raises(ValueError):
        await plugin.skills_sync_bindpaths({"symlinks": [{"source": "/source", "target": "/active/retro/nested"}]})
    assert plugin._skill_links == {"/active/retro": "/source"}


async def test_local_cleanup_rejects_active_link_directory():
    plugin = LocalClaudeCodePluginImpl()
    await plugin.file_upload("/source/SKILL.md", b"skill")
    await plugin.skills_sync_bindpaths({"symlinks": [{"source": "/source", "target": "/active/retro"}]})
    with pytest.raises(ValueError):
        await plugin.skills_clean_symlinks({"directories": ["/active/retro"]})
    assert plugin._skill_links == {"/active/retro": "/source"}
