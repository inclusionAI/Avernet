"""The script, mcp, identity and engine_config entry compilers."""

from __future__ import annotations

import pytest

from agentclaw.community.core.apply_engine.desired_state import McpEndpointEnv, McpHeader, ContentRef
from agentclaw.community.core.bot_config_manifest.compiler.entries.engine_config import compile_engine_config
from agentclaw.community.core.bot_config_manifest.compiler.entries.identity import compile_identity
from agentclaw.community.core.bot_config_manifest.compiler.entries.mcp import compile_mcp
from agentclaw.community.core.bot_config_manifest.compiler.entries.script import compile_script
from agentclaw.community.core.bot_config_manifest.compiler.session import digest_of
from tests.community.core.apply_engine._fakes import oss_source
from tests.community.core.bot_config_manifest.compiler._rig import rig


def test_script_substitutes_placeholders_and_null_declares_none() -> None:
    session, _, _ = rig(engine_type="claude_code")
    declared = compile_script(session, [{"body": "echo ${BOT_ENGINE_TYPE} ${BOT_ENV}"}])
    assert declared.entries[0].body == "echo claude_code dev"
    assert compile_script(session, []).entries == ()
    failed = compile_script(session, [{"body": 7}])
    assert failed.entries == () and failed.failures[0].reason == "'script.body' must be a string"


def test_mcp_overrides_duplicates_and_missing_codes() -> None:
    declared = compile_mcp(
        [
            {"server_code": "gh"},
            {"server_code": "jira", "config": {"url": "https://j", "headers": {"X": "1"}, "endpoint_env": "PRE"}},
            {"server_code": "gh"},
            {"config": {}},
            {"server_code": "empty", "config": {}},
        ]
    )
    assert [e.server_code for e in declared.entries] == ["gh", "jira", "empty"]
    assert declared.entries[0].override is None
    assert declared.entries[1].override.headers == (McpHeader("X", "1"),)
    assert declared.entries[1].override.endpoint_env is McpEndpointEnv.PRE
    assert declared.entries[2].override is None  # an empty mapping is no override, as today
    assert [f.identity for f in declared.failures] == ["gh", "[3]"]
    assert "more than once" in declared.failures[0].reason
    bad = compile_mcp([{"server_code": "x", "config": {"token": "nope"}}])
    assert bad.entries == () and "unknown MCP override members" in bad.failures[0].reason


@pytest.mark.asyncio
async def test_identity_files_inline_content_and_fetches_declared_sources() -> None:
    session, content, resolver = rig({"soul.md": b"# fetched soul\n"})
    declared = await compile_identity(
        session,
        [
            {"type": "SOUL.md", "source": oss_source("soul.md")},
            {"type": "AGENTS.md", "content": "# inline\n"},
            {"type": "AGENTS.md", "content": "again"},
            {"type": "KNOWLEDGE.md"},
            {"content": "no type"},
            {"type": "MISSING.md", "source": oss_source("absent.md")},
        ],
        resolver=resolver,
        content=content,
    )
    assert [e.type for e in declared.entries] == ["SOUL.md", "AGENTS.md"]
    assert declared.entries[0].content == ContentRef(digest_of(b"# fetched soul\n"))
    assert content.read(declared.entries[1].content.digest) == b"# inline\n"
    assert content.is_reachable(session.scope, declared.entries[1].content.digest, produced_by="manifest")
    reasons = {f.identity: f.reason for f in declared.failures}
    assert "more than once" in reasons["AGENTS.md"]
    assert "'source', 'from' or 'content'" in reasons["KNOWLEDGE.md"]
    assert "[4]" in reasons
    assert "MISSING.md" in reasons


def test_engine_config_carries_a_mapping_and_refuses_the_rest() -> None:
    assert compile_engine_config(None).entries == ()
    carried = compile_engine_config({"config": {"model": "x"}})
    assert carried.entries[0].config["model"] == "x"
    assert compile_engine_config({"nope": 1}).failures[0].identity == "engine_config"
    listed = compile_engine_config([{"name": "jq"}, "junk"])
    assert [f.identity for f in listed.failures] == ["jq", "[1]"]
