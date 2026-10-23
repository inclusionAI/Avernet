"""The desired-state model's invariants, the MCP override mapping, and the state reference payloads."""

from __future__ import annotations

import pytest

from agentclaw.community.core.apply_engine.compiler import (
    GenomeStateRef,
    ManifestStateRef,
    state_ref_from_payload,
)
from agentclaw.community.core.apply_engine.constructs import ManifestCategory, ManifestSection
from agentclaw.community.core.apply_engine.desired_state import (
    RESOURCES_ROOT,
    CenterSkillVersion,
    Declaration,
    DesiredCliTool,
    DesiredEngineConfig,
    DesiredIdentityFile,
    DesiredMcpServer,
    DesiredResourceFile,
    DesiredResourceTree,
    DesiredScript,
    DesiredSkill,
    DesiredState,
    EntryFailure,
    LocalSkillPackage,
    McpEndpointEnv,
    McpHeader,
    McpOverride,
    McpTransport,
    RepoSkillPath,
    SkillOrigin,
    StoredContent,
    UnpackKind,
)

DIGEST = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32


def test_stored_content_requires_a_digest() -> None:
    assert StoredContent(DIGEST).digest == DIGEST
    with pytest.raises(ValueError, match="not a content digest"):
        StoredContent("sha256:short")
    with pytest.raises(ValueError, match="not a content digest"):
        StoredContent("md5:" + "ab" * 32)


def test_resource_paths_follow_the_root_and_the_trailing_slash_rule() -> None:
    DesiredResourceFile(RESOURCES_ROOT + "faq.csv", StoredContent(DIGEST))
    DesiredResourceTree(RESOURCES_ROOT + "prices/", StoredContent(DIGEST))
    with pytest.raises(ValueError, match="outside"):
        DesiredResourceFile("notes/hello.txt", StoredContent(DIGEST))
    with pytest.raises(ValueError, match="must not end with"):
        DesiredResourceFile(RESOURCES_ROOT + "prices/", StoredContent(DIGEST))
    with pytest.raises(ValueError, match="must end with"):
        DesiredResourceTree(RESOURCES_ROOT + "prices", StoredContent(DIGEST))
    with pytest.raises(ValueError, match="names the root"):
        DesiredResourceTree(RESOURCES_ROOT, StoredContent(DIGEST))


def test_entries_need_their_identity() -> None:
    with pytest.raises(ValueError, match="server_code"):
        DesiredMcpServer("")
    with pytest.raises(ValueError, match="needs a type"):
        DesiredIdentityFile("", StoredContent(DIGEST))
    with pytest.raises(ValueError, match="needs a name"):
        DesiredSkill("", LocalSkillPackage(StoredContent(DIGEST)))
    with pytest.raises(ValueError, match="needs a name"):
        DesiredCliTool("", StoredContent(DIGEST))
    with pytest.raises(ValueError, match="needs a version"):
        CenterSkillVersion("")
    with pytest.raises(ValueError, match="needs a path"):
        RepoSkillPath("")
    with pytest.raises(ValueError, match="not a content digest"):
        DesiredCliTool("rg", StoredContent(DIGEST), pin="sha256:nope")


def test_a_declaration_refuses_duplicate_identities() -> None:
    gh = DesiredMcpServer("gh")
    with pytest.raises(ValueError, match="duplicate entry 'gh'"):
        Declaration((gh, DesiredMcpServer("gh", McpOverride(url="https://x"))))
    declared = Declaration((gh, DesiredMcpServer("slack")), failures=(EntryFailure("jira", "no permission"),))
    assert declared.identities == ("gh", "slack")
    assert declared.failures[0].reason == "no permission"


def test_skill_origin_is_read_off_the_source() -> None:
    assert DesiredSkill("a", LocalSkillPackage(StoredContent(DIGEST))).origin is SkillOrigin.LOCAL
    assert DesiredSkill("b", CenterSkillVersion("v7")).origin is SkillOrigin.CENTER
    assert DesiredSkill("c", RepoSkillPath("team/weather")).origin is SkillOrigin.REPO


def test_mcp_override_round_trips_the_ports_mapping() -> None:
    config = {
        "url": "https://mcp.example.com/sse",
        "headers": {"Authorization": "Bearer x", "X-Team": "ops"},
        "endpoint_env": "PRE",
        "transport_protocol": "STREAMABLE_HTTP",
    }
    override = McpOverride.from_config(config)
    assert override.headers == (McpHeader("Authorization", "Bearer x"), McpHeader("X-Team", "ops"))
    assert override.endpoint_env is McpEndpointEnv.PRE
    assert override.transport is McpTransport.STREAMABLE_HTTP
    assert override.as_config() == config
    assert McpOverride().as_config() == {}
    assert McpOverride.from_config({"url": "https://only"}).as_config() == {"url": "https://only"}
    with pytest.raises(ValueError, match="unknown MCP override members"):
        McpOverride.from_config({"token": "x"})
    with pytest.raises(ValueError):
        McpOverride.from_config({"endpoint_env": "STAGING"})


def test_state_declares_per_construct_and_none_means_untouched() -> None:
    state = DesiredState(
        script=Declaration((DesiredScript("echo hi"),)),
        mcp=Declaration(()),
        resources=Declaration((DesiredResourceFile(RESOURCES_ROOT + "a.txt", StoredContent(DIGEST)),)),
    )
    assert state.declaration(ManifestSection.SCRIPT).entries[0].body == "echo hi"
    assert state.declaration(ManifestCategory.MCP).entries == ()
    assert state.declaration(ManifestCategory.SKILLS) is None
    assert state.declared == (ManifestSection.SCRIPT, ManifestCategory.RESOURCES, ManifestCategory.MCP)
    assert DesiredState.EMPTY.declared == ()
    # A section has one fixed identity, so a second entry is a duplicate by construction.
    with pytest.raises(ValueError, match="duplicate entry 'script'"):
        DesiredState(script=Declaration((DesiredScript("a"), DesiredScript("b"))))
    engine = DesiredState(engine_config=Declaration((DesiredEngineConfig({"model": "x"}),)))
    assert engine.declaration(ManifestCategory.ENGINE_CONFIG).entries[0].identity == "engine_config"


def test_cli_tool_pin_and_unpack() -> None:
    tool = DesiredCliTool("rg", StoredContent(DIGEST), pin=OTHER, subpath="bin/rg", unpack=UnpackKind.TAR_GZ)
    assert tool.identity == "rg"
    assert tool.pin == OTHER
    assert UnpackKind("tar.gz") is UnpackKind.TAR_GZ


def test_state_reference_payloads_round_trip() -> None:
    assert ManifestStateRef().to_payload() == {"kind": "manifest"}
    assert GenomeStateRef("p1").to_payload() == {"kind": "genome", "promotion_id": "p1"}
    assert state_ref_from_payload(None) == ManifestStateRef()
    assert state_ref_from_payload({"kind": "manifest"}) == ManifestStateRef()
    assert state_ref_from_payload({"kind": "genome", "promotion_id": "p1"}) == GenomeStateRef("p1")
    with pytest.raises(ValueError, match="unknown state reference kind"):
        state_ref_from_payload({"kind": "svn"})
    with pytest.raises(ValueError, match="promotion id"):
        GenomeStateRef("")
