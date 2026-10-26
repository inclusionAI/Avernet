"""The desired-state model's invariants, the MCP override mapping, and the state reference payloads."""

from __future__ import annotations

import pytest

from agentclaw.community.core.apply_engine.compiler import (
    LEGACY_STATE,
    StateRef,
    state_ref_from_payload,
)
from agentclaw.community.core.apply_engine.constructs import Category, Section
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
    ContentRef,
    UnpackKind,
)

DIGEST = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32


def test_stored_content_requires_a_digest() -> None:
    assert ContentRef(DIGEST).digest == DIGEST
    with pytest.raises(ValueError, match="not a content digest"):
        ContentRef("sha256:short")
    with pytest.raises(ValueError, match="not a content digest"):
        ContentRef("md5:" + "ab" * 32)


def test_resource_paths_follow_the_root_and_the_trailing_slash_rule() -> None:
    DesiredResourceFile(RESOURCES_ROOT + "faq.csv", ContentRef(DIGEST))
    DesiredResourceTree(RESOURCES_ROOT + "prices/", ContentRef(DIGEST))
    with pytest.raises(ValueError, match="outside"):
        DesiredResourceFile("notes/hello.txt", ContentRef(DIGEST))
    with pytest.raises(ValueError, match="must not end with"):
        DesiredResourceFile(RESOURCES_ROOT + "prices/", ContentRef(DIGEST))
    with pytest.raises(ValueError, match="must end with"):
        DesiredResourceTree(RESOURCES_ROOT + "prices", ContentRef(DIGEST))
    with pytest.raises(ValueError, match="names the root"):
        DesiredResourceTree(RESOURCES_ROOT, ContentRef(DIGEST))


def test_entries_need_their_identity() -> None:
    with pytest.raises(ValueError, match="server_code"):
        DesiredMcpServer("")
    with pytest.raises(ValueError, match="needs a type"):
        DesiredIdentityFile("", ContentRef(DIGEST))
    with pytest.raises(ValueError, match="needs a name"):
        DesiredSkill("", LocalSkillPackage(ContentRef(DIGEST)))
    with pytest.raises(ValueError, match="needs a name"):
        DesiredCliTool("", ContentRef(DIGEST))
    with pytest.raises(ValueError, match="needs a version"):
        CenterSkillVersion("")
    with pytest.raises(ValueError, match="needs a path"):
        RepoSkillPath("")
    with pytest.raises(ValueError, match="not a content digest"):
        DesiredCliTool("rg", ContentRef(DIGEST), pin="sha256:nope")


def test_a_declaration_refuses_duplicate_identities() -> None:
    gh = DesiredMcpServer("gh")
    with pytest.raises(ValueError, match="duplicate entry 'gh'"):
        Declaration((gh, DesiredMcpServer("gh", McpOverride(url="https://x"))))
    declared = Declaration((gh, DesiredMcpServer("slack")), failures=(EntryFailure("jira", "no permission"),))
    assert declared.identities == ("gh", "slack")
    assert declared.failures[0].reason == "no permission"


def test_skill_origin_is_read_off_the_source() -> None:
    assert DesiredSkill("a", LocalSkillPackage(ContentRef(DIGEST))).origin is SkillOrigin.LOCAL
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
        resources=Declaration((DesiredResourceFile(RESOURCES_ROOT + "a.txt", ContentRef(DIGEST)),)),
    )
    assert state.declaration(Section.SCRIPT).entries[0].body == "echo hi"
    assert state.declaration(Category.MCP).entries == ()
    assert state.declaration(Category.SKILLS) is None
    assert state.declared == (Section.SCRIPT, Category.RESOURCES, Category.MCP)
    assert DesiredState.EMPTY.declared == ()
    # A section has one fixed identity, so a second entry is a duplicate by construction.
    with pytest.raises(ValueError, match="duplicate entry 'script'"):
        DesiredState(script=Declaration((DesiredScript("a"), DesiredScript("b"))))
    engine = DesiredState(engine_config=Declaration((DesiredEngineConfig({"model": "x"}),)))
    assert engine.declaration(Category.ENGINE_CONFIG).entries[0].identity == "engine_config"


def test_cli_tool_pin_and_unpack() -> None:
    tool = DesiredCliTool("rg", ContentRef(DIGEST), pin=OTHER, subpath="bin/rg", unpack=UnpackKind.TAR_GZ)
    assert tool.identity == "rg"
    assert tool.pin == OTHER
    assert UnpackKind("tar.gz") is UnpackKind.TAR_GZ


def test_state_reference_payloads_round_trip() -> None:
    assert StateRef("manifest").to_payload() == {"kind": "manifest", "reference": None}
    assert StateRef("genome", "p1").to_payload() == {"kind": "genome", "reference": "p1"}
    assert state_ref_from_payload(None) == LEGACY_STATE == StateRef("manifest")
    assert state_ref_from_payload({"kind": "manifest", "reference": None}) == StateRef("manifest")
    assert state_ref_from_payload({"kind": "genome", "reference": "p1"}) == StateRef("genome", "p1")
    # The engine carries any kind; which kinds exist is the compiler map's business.
    assert state_ref_from_payload({"kind": "svn"}) == StateRef("svn")
    with pytest.raises(ValueError, match="needs a kind"):
        state_ref_from_payload({"reference": "p1"})
    with pytest.raises(ValueError, match="needs a kind"):
        StateRef("")


def test_the_producers_build_their_own_references() -> None:
    from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import MANIFEST_STATE
    from agentclaw.community.core.bot_genome.compile import GENOME_STATE_KIND, genome_state

    assert MANIFEST_STATE == LEGACY_STATE, "rows that predate the reference were Manifest applies"
    assert genome_state("p1") == StateRef(GENOME_STATE_KIND, "p1")
    with pytest.raises(ValueError, match="promotion id"):
        genome_state("")


def test_resource_paths_are_normalised_workspace_relative_paths() -> None:
    for bad in ("data/../SOUL.md", "data//x", "data/./x", "/data/x", "data/a b"):
        with pytest.raises(ValueError):
            DesiredResourceFile(bad, ContentRef(DIGEST))


def test_a_directory_entry_owns_its_subtree() -> None:
    tree = DesiredResourceTree(RESOURCES_ROOT + "prices/", ContentRef(DIGEST))
    nested = DesiredResourceFile(RESOURCES_ROOT + "prices/2026.csv", ContentRef(OTHER))
    same = DesiredResourceFile(RESOURCES_ROOT + "prices", ContentRef(OTHER))
    with pytest.raises(ValueError, match="nested under directory entry"):
        DesiredState(resources=Declaration((tree, nested)))
    with pytest.raises(ValueError, match="nested under directory entry"):
        DesiredState(resources=Declaration((same, tree)))
    DesiredState(
        resources=Declaration((tree, DesiredResourceFile(RESOURCES_ROOT + "prices.csv", ContentRef(OTHER))))
    )


def test_mcp_override_keeps_an_explicit_empty_headers_override() -> None:
    assert McpOverride.from_config({"headers": {}}).as_config() == {"headers": {}}
    assert McpOverride.from_config({"url": "https://x"}).headers is None
    with pytest.raises(ValueError, match="case-insensitive"):
        McpOverride(headers=(McpHeader("Authorization", "a"), McpHeader("authorization", "b")))


def test_engine_config_is_a_read_only_copy() -> None:
    raw = {"model": "x"}
    entry = DesiredEngineConfig(raw)
    raw["model"] = "y"
    assert entry.config["model"] == "x"
    with pytest.raises(TypeError):
        entry.config["model"] = "z"  # type: ignore[index]


def test_only_keeps_the_named_constructs_declared_and_undeclares_the_rest() -> None:
    """A creation phase applies half the constructs; ``only`` is how a compiled
    state is narrowed to that half without recompiling."""
    state = DesiredState(
        script=Declaration((DesiredScript("echo"),)),
        identity=Declaration((DesiredIdentityFile("SOUL.md", ContentRef(DIGEST)),)),
        mcp=Declaration(()),
    )
    half = state.only({Section.SCRIPT, Category.MCP})
    assert half.declared == (Section.SCRIPT, Category.MCP)
    assert half.script == state.script and half.mcp == state.mcp and half.identity is None
    assert state.only(()) == DesiredState.EMPTY
    assert state.only(state.declared) == state
