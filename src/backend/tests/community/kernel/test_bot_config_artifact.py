"""Unit tests for the published ``BotConfigArtifact`` contract.

Covers: serialization round-trip, and conformance of produced artifacts to
the language-neutral source of truth ``artifact.schema.json`` (the same schema
the external engine owner codes against — Task 16 also reuses this oracle).
"""
from __future__ import annotations

import json
import pathlib

import jsonschema
import pytest

from agentclaw.community.kernel.bot_config import (
    SCHEMA_VERSION,
    BotConfigArtifact,
    CliToolRef,
    FileRef,
    McpManifest,
    McpServerRef,
    ResourceRef,
    SkillRef,
    StoreRef,
)

_SCHEMA_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "src"
    / "agentclaw"
    / "community"
    / "kernel"
    / "bot_config"
    / "artifact.schema.json"
)


def test_remote_entry_omits_the_launch_keys_entirely() -> None:
    """An artifact with no local server keeps its pre-local-form wire shape.

    ``asdict`` would emit ``"command": null`` / empty ``args``/``env`` on every
    remote entry, changing the bytes of artifacts that never use the local form
    — and a consumer validating those against the pre-local-form definition
    (``additionalProperties: false``) would reject them. Only artifacts that
    genuinely carry a local server may differ.
    """
    artifact = _sample_artifact()
    entry = artifact.to_dict()["mcp"]["servers"][0]

    assert set(entry) == {"server_code", "name", "endpoint", "transport", "headers"}


def test_local_entry_carries_its_launch_instruction_flat() -> None:
    artifact = BotConfigArtifact(
        schema_version=SCHEMA_VERSION,
        engine_type="teclaw",
        mcp=McpManifest(
            servers=[
                McpServerRef(
                    server_code="hitl",
                    name="HITL",
                    transport="stdio",
                    command="python3",
                    args=["/a.py"],
                )
            ]
        ),
    )
    entry = artifact.to_dict()["mcp"]["servers"][0]

    # The launch instruction is flat on the entry — no nested "stdio" object.
    assert "stdio" not in entry
    assert entry == {
        "server_code": "hitl",
        "name": "HITL",
        "endpoint": None,
        "transport": "stdio",
        "headers": {},
        "command": "python3",
        "args": ["/a.py"],
        "env": {},
    }


def test_from_dict_reflattens_a_legacy_nested_stdio_entry() -> None:
    """An artifact pinned before the flat local form (nested ``{"stdio":
    {...}}``) still loads — its launch instruction lands on the flat fields."""
    restored = BotConfigArtifact.from_dict(
        {
            "schema_version": 4,
            "engine_type": "teclaw",
            "mcp": {
                "servers": [
                    {
                        "server_code": "hitl",
                        "transport": "stdio",
                        "stdio": {"command": "python3", "args": ["/a.py"], "env": {}},
                    }
                ]
            },
        }
    )
    server = restored.mcp.servers[0]
    assert server.command == "python3"
    assert server.args == ["/a.py"]
    assert server.env == {}


def _sample_artifact() -> BotConfigArtifact:
    return BotConfigArtifact(
        schema_version=SCHEMA_VERSION,
        engine_type="teclaw",
        version=7,
        mcp=McpManifest(
            servers=[
                # "http" — not "STREAMABLE_HTTP". That is MCP Center's endpoint
                # vocabulary; ``McporterComposer._select_endpoint`` maps it onto
                # the artifact's own transport values ("http" / "sse"), which are
                # what the discriminator in the schema enumerates.
                McpServerRef(
                    server_code="mcp.ant.faas.xxx",
                    endpoint="https://example/mcp",
                    transport="http",
                    headers={"x-ling-auth": "ak-antchat-inlined"},
                )
            ]
        ),
        skills=[
            SkillRef(name="odps-sql-generator", scope="shared", store="skill-repo", path="team/odps"),
            SkillRef(name="my-skill", scope="user", store="user-nas", path="skills/my-skill"),
        ],
        resources=[ResourceRef(name="r1", store="user-nas", path="resources/r1")],
        identity_files=[FileRef(name="RULES.md", store="user-nas", path="identity/RULES.md")],
        stores={
            "skill-repo": StoreRef(
                type="oss",
                bucket="antsys-agentclaw-prod",
                base="aidesktop/aidesktop_prod/bolt_shared/skills-repo",
            ),
            "user-nas": StoreRef(type="nas", base="/home/admin/nfs/bot-data"),
        },
        engine_overrides={"channels": {"dingtalk": {"enabled": True}}},
        engine_ext={"memory_ref": "nas://ws/MEMORY.md", "anything": [1, 2, 3]},
    )


@pytest.mark.unit
def test_to_dict_from_dict_roundtrip_is_identity() -> None:
    artifact = _sample_artifact()
    restored = BotConfigArtifact.from_dict(artifact.to_dict())
    assert restored == artifact


@pytest.mark.unit
def test_to_dict_is_json_serializable() -> None:
    payload = json.dumps(_sample_artifact().to_dict())
    assert '"schema_version"' in payload


@pytest.mark.unit
def test_produced_artifact_conforms_to_schema() -> None:
    schema = json.loads(_SCHEMA_PATH.read_text())
    jsonschema.validate(instance=_sample_artifact().to_dict(), schema=schema)


@pytest.mark.unit
def test_minimal_artifact_conforms_to_schema() -> None:
    """A personal/draft bot: no version, empty collections."""
    minimal = BotConfigArtifact(schema_version=SCHEMA_VERSION, engine_type="openclaw")
    schema = json.loads(_SCHEMA_PATH.read_text())
    jsonschema.validate(instance=minimal.to_dict(), schema=schema)


@pytest.mark.unit
def test_secret_is_inlined_into_the_server_entry() -> None:
    """The resolved MCP credential is inlined into the entry (header or endpoint
    query); there is no by-reference ``auth_ref`` field anymore."""
    server = _sample_artifact().mcp.servers[0]
    assert not hasattr(server, "auth_ref")
    assert server.headers["x-ling-auth"] == "ak-antchat-inlined"


@pytest.mark.unit
def test_schema_version_distinct_from_content_version() -> None:
    artifact = _sample_artifact()
    assert artifact.schema_version == SCHEMA_VERSION
    assert artifact.version == 7
    assert artifact.schema_version != artifact.version


@pytest.mark.unit
def test_stores_carry_location_only_no_credentials() -> None:
    """Store coords hold bucket/base/endpoint — never credentials."""
    store = _sample_artifact().stores["skill-repo"]
    assert store.type == "oss"
    assert store.bucket == "antsys-agentclaw-prod"
    # Scan the stores block only: MCP entries now legitimately carry inlined
    # credentials, so the whole-artifact scan no longer applies — stores must
    # still be credential-free.
    serialized = json.dumps(_sample_artifact().to_dict()["stores"])
    for forbidden in ("access_key", "secret", "password", "credential", "token"):
        assert forbidden not in serialized


@pytest.mark.unit
def test_refs_store_keys_resolve_against_stores() -> None:
    """Every file ref's 'store' names a store id present in 'stores', and each
    carries a relative 'path' within that store."""
    artifact = _sample_artifact()
    refs = [*artifact.skills, *artifact.resources, *artifact.identity_files]
    for ref in refs:
        assert ref.store in artifact.stores
        assert ref.path  # relative path within the store


@pytest.mark.unit
def test_example_artifact_conforms_and_roundtrips() -> None:
    """The in-code EXAMPLE_ARTIFACT (living documentation) is valid against the
    schema and survives a serialize round-trip."""
    from agentclaw.community.kernel.bot_config.artifact import EXAMPLE_ARTIFACT

    schema = json.loads(_SCHEMA_PATH.read_text())
    jsonschema.validate(instance=EXAMPLE_ARTIFACT.to_dict(), schema=schema)
    assert BotConfigArtifact.from_dict(EXAMPLE_ARTIFACT.to_dict()) == EXAMPLE_ARTIFACT
    assert EXAMPLE_ARTIFACT.schema_version == SCHEMA_VERSION


@pytest.mark.unit
def test_undeclared_cli_tools_leaves_the_key_off_the_wire() -> None:
    """Absence and ``[]`` are different instructions, so absence must stay absent.

    ``asdict`` would put ``"cli_tools": []`` on every artifact. Under the
    convergence contract an explicit empty array orders the applier to remove
    every manifest-delivered tool, so an unrelated change — a skill edit, say —
    would ship a wipe order for tools the bot is actively using. An artifact
    that declares nothing must be byte-identical to one built before the field
    existed.
    """
    artifact = BotConfigArtifact(schema_version=SCHEMA_VERSION, engine_type="teclaw")

    assert artifact.cli_tools is None
    assert "cli_tools" not in artifact.to_dict()


@pytest.mark.unit
def test_declared_empty_cli_tools_is_carried_as_an_explicit_wipe() -> None:
    """The other half of the same rule: ``[]`` is a declaration and must ship."""
    artifact = BotConfigArtifact(
        schema_version=SCHEMA_VERSION, engine_type="teclaw", cli_tools=[]
    )

    assert artifact.to_dict()["cli_tools"] == []


@pytest.mark.unit
def test_cli_tool_entry_carries_one_file_per_command() -> None:
    artifact = BotConfigArtifact(
        schema_version=SCHEMA_VERSION,
        engine_type="teclaw",
        cli_tools=[
            CliToolRef(
                name="mycli",
                store="bot-data",
                path="staff_u1/bot7/teclaw/cli/mycli",
                md5="9f2c1b7d4e5a60318c2f0ab4d7e9c135",
                version="1.4.2",
            )
        ],
    )

    entry = artifact.to_dict()["cli_tools"][0]
    assert entry == {
        "name": "mycli",
        "store": "bot-data",
        "path": "staff_u1/bot7/teclaw/cli/mycli",
        "md5": "9f2c1b7d4e5a60318c2f0ab4d7e9c135",
        "version": "1.4.2",
    }


@pytest.mark.unit
def test_from_dict_does_not_manufacture_an_empty_declaration() -> None:
    """Reading a v4 artifact must yield "not declared", not "wipe everything".

    Round-tripping is where this would bite: had ``from_dict`` defaulted to
    ``[]``, a v4 artifact read and re-emitted would come back out carrying an
    explicit wipe order the original never contained.
    """
    v4_payload = {"schema_version": 4, "engine_type": "teclaw"}

    restored = BotConfigArtifact.from_dict(v4_payload)

    assert restored.cli_tools is None
    assert "cli_tools" not in restored.to_dict()


@pytest.mark.unit
def test_cli_tools_roundtrip_preserves_both_states() -> None:
    for declared in ([], [CliToolRef(name="t", store="s", path="p", md5="d")]):
        artifact = BotConfigArtifact(
            schema_version=SCHEMA_VERSION, engine_type="teclaw", cli_tools=declared
        )
        assert BotConfigArtifact.from_dict(artifact.to_dict()) == artifact


@pytest.mark.unit
def test_cli_tools_artifacts_conform_to_schema() -> None:
    schema = json.loads(_SCHEMA_PATH.read_text())
    for declared in ([], [CliToolRef(name="t", store="s", path="p", md5="d")]):
        artifact = BotConfigArtifact(
            schema_version=SCHEMA_VERSION, engine_type="teclaw", cli_tools=declared
        )
        jsonschema.validate(instance=artifact.to_dict(), schema=schema)


@pytest.mark.unit
def test_schema_version_stays_at_4_until_cli_tools_ship() -> None:
    """The shape is declared; the version bump is deliberately not taken yet.

    ``ConfigComposer`` stamps ``SCHEMA_VERSION`` onto every artifact it builds,
    so bumping it here ships ``"schema_version": 5`` to engines running today —
    which is exactly what ``teclaw-cli-contract.zh-CN.md`` §6 promises teclaw
    will not happen. Bump it in the change that first populates ``cli_tools``,
    once teclaw has confirmed it accepts v5.
    """
    assert SCHEMA_VERSION == 4
