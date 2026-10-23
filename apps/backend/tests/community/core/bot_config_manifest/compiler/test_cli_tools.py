"""The cli_tools entry compiler: the digest belt, the pinned fast path, the fetched bytes."""

from __future__ import annotations

import pytest

from agentclaw.community.core.apply_engine.desired_state import StoredContent, UnpackKind
from agentclaw.community.core.bot_config_manifest.compiler.entries.cli_tools import compile_cli_tools
from agentclaw.community.core.bot_config_manifest.compiler.session import digest_of
from tests.community.core.apply_engine._fakes import OSS_BUCKET, oss_source
from tests.community.core.bot_config_manifest.compiler._rig import rig

BINARY = b"\x7fELF-not-really"


@pytest.mark.asyncio
async def test_pinned_tools_are_fetched_once_and_carry_the_install_shape() -> None:
    session, content, resolver = rig({"jq.tgz": BINARY})
    entry = {
        "name": "jq",
        "source": oss_source("jq.tgz"),
        "digest": digest_of(BINARY),
        "unpack": "tar.gz",
        "subpath": "bin/jq",
        "version": "1.7",
    }
    first = await compile_cli_tools(session, [entry], resolver=resolver, content=content)
    assert first.failures == ()
    (tool,) = first.entries
    assert tool.content == StoredContent(digest_of(BINARY))
    assert tool.pin == digest_of(BINARY)
    assert tool.unpack is UnpackKind.TAR_GZ and tool.subpath == "bin/jq" and tool.version == "1.7"
    assert tool.declared_address == f"{OSS_BUCKET}/jq.tgz"
    fetches = len(content.store_calls)
    again = await compile_cli_tools(session, [entry], resolver=resolver, content=content)
    assert again.entries[0].content == tool.content
    assert len(content.store_calls) == fetches  # reachable already: no second fetch, no second receipt


@pytest.mark.asyncio
async def test_the_digest_belt_and_the_grammar_are_re_asked() -> None:
    session, content, resolver = rig({"rg": BINARY})
    declared = await compile_cli_tools(
        session,
        [
            {"name": "rg", "source": oss_source("rg")},
            {"name": "dup", "source": oss_source("rg"), "digest": digest_of(BINARY)},
            {"name": "dup", "source": oss_source("rg"), "digest": digest_of(BINARY)},
            {"source": oss_source("rg")},
            {"name": "weird", "source": oss_source("rg"), "digest": digest_of(BINARY), "unpack": "rar"},
            {"name": "gone", "source": oss_source("absent"), "digest": digest_of(b"other")},
        ],
        resolver=resolver,
        content=content,
    )
    assert [e.name for e in declared.entries] == ["dup"]
    reasons = {f.identity: f.reason for f in declared.failures}
    assert "requires a 'digest'" in reasons["rg"]
    assert "more than once" in reasons["dup"]
    assert "[3]" in reasons
    assert "unknown unpack kind" in reasons["weird"]
    assert "gone" in reasons


@pytest.mark.asyncio
async def test_a_malformed_pin_fails_the_entry_not_the_compile() -> None:
    session, content, resolver = rig({"tools/mycli": b"\x7fELF"})
    declared = await compile_cli_tools(
        session,
        [{"name": "mycli", "source": oss_source("tools/mycli"), "digest": "abc123"}],
        resolver=resolver,
        content=content,
    )
    assert declared.entries == ()
    assert [(f.identity, f.reason) for f in declared.failures] == [
        ("mycli", "digest must be 'sha256:' followed by 64 lowercase hex characters")
    ]
