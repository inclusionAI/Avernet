"""README §6.4: every apply-time predicate, on the contract's invalid cases and with fakes of the MCP services."""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import canonical_tree_bytes
from agentclaw.community.core.bot_genome.limits import MAX_PERSONA_BYTES
from agentclaw.community.core.bot_genome.limits import MAX_SCRIPT_BYTES
from agentclaw.community.core.bot_genome.spec_checks import (
    SpecCheckContext,
    SpecChecks,
    mcp_entry_reasons,
    order_reasons,
    persona_reasons,
    policy_path_reasons,
    resource_reasons,
    schema_reasons,
    skill_reasons,
    tree_reasons,
)
from agentclaw.community.core.bot_genome.spec_models import (
    GenomePolicy,
    GenomeSpec,
    LocalSkill,
    McpConfig,
    McpServerEntry,
    MemorySpec,
    ResourceEntry,
    ScriptEntry,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from tests.community.core.bot_genome.example_blobs import (
    SUPPORT_BOT,
    BlobDir,
    load_invalid,
    load_revisions,
    policy_of,
    spec_of,
)

REVISIONS = load_revisions()
INVALID = load_invalid()
CTX = SpecCheckContext(engine_type="openclaw", bot_type="personal", owner_id="user_owner_5", actor_id="user_owner_5")
#: The contract's ``check`` names → the predicate code that refuses them.
CODES = {
    "utf8": "not_utf8",
    "skill-package": "skill_package",
    "admission": "admission",
    "resource-paths": "resource_paths",
    "tree": "tree",
    "order": "order",
    "canonical-path": "not_canonical",
}


class FakeAuth:
    def __init__(self, permitted: bool = True, raising: bool = False) -> None:
        self.permitted, self.raising, self.calls = permitted, raising, []

    def check_mcp_permission_detail(self, user_id: str, server_code: str) -> dict[str, Any]:
        self.calls.append((user_id, server_code))
        if self.raising:
            raise RuntimeError("marketplace unreachable")
        return {"has_permission": self.permitted, "access_level": "PUBLIC" if self.permitted else ""}


class FakeConfig:
    def __init__(self, valid: bool = True) -> None:
        self.valid, self.calls = valid, []

    def validate_bot_override(
        self, *, user_id: str, server_code: str, config: Any, engine_type: str | None = None
    ) -> dict[str, Any]:
        self.calls.append((user_id, server_code, config, engine_type))
        return {"valid": True} if self.valid else {"valid": False, "error": "no remote config on a LOCAL server"}


def _checks(auth: FakeAuth | None = None, config: FakeConfig | None = None) -> SpecChecks:
    return SpecChecks(
        validator=SkillPackageValidator(SkillParser()), mcp_auth=auth or FakeAuth(), mcp_config=config or FakeConfig()
    )


def _record() -> tuple[GenomeSpec, GenomePolicy]:
    return spec_of(SUPPORT_BOT), policy_of(SUPPORT_BOT)


def _parsed(record: dict[str, Any]) -> tuple[GenomeSpec, GenomePolicy]:
    return GenomeSpec.from_json(record["spec"]), GenomePolicy.from_json(record["policy"])


# -- the contract's examples --------------------------------------------------------------------


@pytest.mark.parametrize("record_id", sorted(REVISIONS), ids=lambda i: i[:15])
def test_every_valid_example_passes(record_id: str) -> None:
    spec, policy = _parsed(REVISIONS[record_id])
    engine = "claude_code" if spec.persona and spec.persona[0].type == "CLAUDE.md" else "openclaw"
    ctx = SpecCheckContext(engine_type=engine, bot_type="personal", owner_id="o", actor_id="a")
    assert _checks().reasons(spec, policy, BlobDir(), ctx) == []


@pytest.mark.parametrize("name", sorted(n for n, c in INVALID.items() if c["check"] in CODES))
def test_every_invalid_example_naming_a_predicate_is_refused_by_it(name: str) -> None:
    case = INVALID[name]
    spec, policy = _parsed(case["record"])
    reasons = _checks().reasons(spec, policy, BlobDir(), CTX)
    assert CODES[case["check"]] in {r.code for r in reasons}, (case["reason"], reasons)


@pytest.mark.parametrize("name", ["mcp-url-no-host", "mcp-url-userinfo", "mcp-header-newline"])
def test_mcp_examples_fail_the_entry_rules_too(name: str) -> None:
    """These three fail the contract pattern, so the typed model refuses them; a spec built past the model still fails
    the Manifest rule.
    """
    record = INVALID[name]["record"]
    spec, _ = _record()
    spec.tools.mcp = [
        McpServerEntry.model_construct(
            server_code=entry["server_code"],
            config=McpConfig.model_construct(**entry["config"]) if "config" in entry else None,
        )
        for entry in record["spec"]["tools"]["mcp"]
    ]
    reasons = mcp_entry_reasons(spec, CTX)
    assert reasons and all(r.code == "mcp_entry" for r in reasons)
    assert all(r.location.startswith("spec.tools.mcp[0].config") for r in reasons)


# -- shape and order -------------------------------------------------------------------------------


def test_schema_reasons_name_the_field() -> None:
    spec, policy = _record()
    # ``model_construct`` skips validation: a record built in code can hold what the parser refuses
    bad_spec = spec.model_copy(update={"engine_config": {"x": 1}})
    assert [(r.code, r.location) for r in schema_reasons(bad_spec, policy)] == [("schema", "spec.engine_config")]
    bad_policy = policy.model_copy(update={"pins": "nope"})
    assert schema_reasons(bad_spec, bad_policy)[-1].location == "policy.pins"


def test_order_and_canonical_paths() -> None:
    spec, policy = _record()
    spec.tools.mcp.reverse()
    assert [r.location for r in order_reasons(spec, policy)] == ["spec.tools.mcp"]
    assert [r.location for r in order_reasons(spec, policy.model_copy(update={"pins": ["a", "a"]}))] == [
        "spec.tools.mcp",
        "policy.pins",
    ]
    assert [
        r.location for r in policy_path_reasons(policy.model_copy(update={"risk_overrides": {"skills/%61": "T2"}}))
    ] == ["policy.risk_overrides"]


# -- persona ---------------------------------------------------------------------------------------


def test_persona_type_the_engine_does_not_accept() -> None:
    spec, _ = _record()
    reasons = persona_reasons(spec, BlobDir(), "claude_code")
    assert {r.code for r in reasons} == {"persona_type"} and len(reasons) == 4
    assert persona_reasons(spec, BlobDir(), "openclaw") == []


def test_a_persona_file_over_the_identity_cap_is_refused() -> None:
    spec, _ = _record()
    blobs = BlobDir()
    spec.persona[0].digest = blobs.put(b"x" * (MAX_PERSONA_BYTES + 1))
    assert [r.code for r in persona_reasons(spec, blobs, "openclaw")] == ["limit_exceeded"]
    spec.persona[0].digest = blobs.put(b"y" * MAX_PERSONA_BYTES)
    assert persona_reasons(spec, blobs, "openclaw") == []


def test_unreachable_content_is_a_reason_not_an_exception() -> None:
    spec, _ = _record()
    spec.persona[0].digest = "sha256:" + "1" * 64
    assert [r.code for r in persona_reasons(spec, BlobDir(), "openclaw")] == ["content_not_reachable"]


# -- trees and skills ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("members", "problem"),
    [
        ([("b.md", b"1"), ("a.md", b"2")], "canonical member order"),
        ([("a.md", b"1"), ("a.md", b"1")], "one path twice"),
        ([("a\x00.md", b"1")], "control character"),
        ([("/a.md", b"1")], "relative"),
        ([("a/../b.md", b"1")], "'..' segment"),
        ([("x" * 256 + ".md", b"1")], "255 bytes"),
        ([("faq.md", b"1"), ("faq.md/a.md", b"2")], "also the directory of"),
    ],
)
def test_tree_rules(members: list[tuple[str, bytes]], problem: str) -> None:
    magic = b"acm-tree-v1\n"
    blob = magic + b"".join(b"%d:%s%d:%s" % (len(p.encode()), p.encode(), len(d), d) for p, d in members)
    reasons, _ = tree_reasons("here", blob)
    assert reasons and reasons[0].code == "tree" and problem in reasons[0].message


def test_not_a_tree() -> None:
    reasons, members = tree_reasons("here", b"PK\x03\x04")
    assert [r.code for r in reasons] == ["tree"] and members == []


def test_skill_tree_must_be_what_the_validator_keeps() -> None:
    validator = SkillPackageValidator(SkillParser())
    blobs = BlobDir()
    skill_md = b"---\nname: tidy\ndescription: Tidy things.\n---\nBody.\n"
    good = blobs.put(canonical_tree_bytes([("SKILL.md", skill_md)]))
    with_junk = blobs.put(canonical_tree_bytes([(".DS_Store", b"x"), ("SKILL.md", skill_md)]))
    no_description = blobs.put(canonical_tree_bytes([("SKILL.md", b"---\nname: tidy\ndescription: ''\n---\n")]))
    spec, _ = _record()
    spec.skills = [LocalSkill(name="tidy", origin="local", digest=good)]
    assert skill_reasons(spec, blobs, validator) == []
    spec.skills = [LocalSkill(name="other", origin="local", digest=good)]
    assert "names 'tidy'" in skill_reasons(spec, blobs, validator)[0].message
    spec.skills = [LocalSkill(name="tidy", origin="local", digest=with_junk)]
    assert "validator keeps" in skill_reasons(spec, blobs, validator)[0].message
    spec.skills = [LocalSkill(name="tidy", origin="local", digest=no_description)]
    assert skill_reasons(spec, blobs, validator)[0].code == "skill_package"


# -- resources -------------------------------------------------------------------------------------


def test_resource_rules() -> None:
    blobs = BlobDir()
    spec, _ = _record()
    spec.resources = [
        ResourceEntry(path="data/faq.csv", digest=blobs.put(b"q,a\n")),
        ResourceEntry(path="data/tool.sh", digest=blobs.put(b"#!/bin/sh\n")),
        ResourceEntry(path="docs/", digest=blobs.put(canonical_tree_bytes([("a.md", b"a"), ("run.toml", b"x")]))),
    ]
    reasons = resource_reasons(spec, blobs)
    assert [(r.code, r.location) for r in reasons] == [
        ("admission", "spec.resources[1]:'data/tool.sh'"),
        ("admission", "spec.resources[2]:'docs/run.toml'"),
    ]
    spec.resources = [
        ResourceEntry(path="data/", digest=blobs.put(b"q,a\n")),
        ResourceEntry(path="data/x.md", digest=blobs.put(b"x")),
    ]
    reasons = resource_reasons(spec, blobs)
    assert [r.code for r in reasons] == ["resource_paths", "tree"]


# -- MCP -------------------------------------------------------------------------------------------


def test_mcp_entry_rules_with_hand_built_entries() -> None:
    """Past the model (``model_construct`` skips validation, as a stored row from an older rule set would): the Manifest
    rules still refuse.
    """
    spec, _ = _record()
    raw = [
        {"server_code": "a", "config": {"url": "ftp://x.example.com/"}},
        {"server_code": "b", "config": {"headers": {"X-A": "1", "x-a": "2"}}},
        {"server_code": "c", "config": {"headers": {"X-A\r": "1"}}},
        {"server_code": "d", "config": {"url": "https://user:pw@x.example.com/mcp"}},
        {
            "server_code": "e",
            "config": {
                "url": "https://x.example.com/mcp",
                "headers": {"X-A": "1"},
                "transport_protocol": "SSE",
                "endpoint_env": "PROD",
            },
        },
    ]
    spec.tools.mcp = [
        McpServerEntry.model_construct(server_code=e["server_code"], config=McpConfig.model_construct(**e["config"]))
        for e in raw
    ]
    reasons = mcp_entry_reasons(spec, CTX)
    assert sorted(r.location for r in reasons) == [
        "spec.tools.mcp[0].config.url",
        "spec.tools.mcp[1].config.headers.x-a",
        "spec.tools.mcp[2].config.headers.X-A\r",
        "spec.tools.mcp[3].config.url",
    ]
    assert spec.tools.mcp[4].config.transport_protocol == "SSE", "the entry is not modified"


def test_mcp_context_checks_ask_the_services_as_apply_does() -> None:
    spec, policy = _record()
    auth, config = FakeAuth(), FakeConfig()
    assert _checks(auth, config).reasons(spec, policy, BlobDir(), CTX) == []
    assert auth.calls == [("user_owner_5", "mcp.x.meet"), ("user_owner_5", "mcp.x.orders")]
    assert config.calls[0] == ("user_owner_5", "mcp.x.meet", spec.tools.mcp[0].config.to_json(), "openclaw")
    assert config.calls[1] == ("user_owner_5", "mcp.x.orders", None, "openclaw")


@pytest.mark.parametrize(
    ("auth", "config", "codes"),
    [
        (FakeAuth(permitted=False), FakeConfig(), ["mcp_not_permitted", "mcp_not_permitted"]),
        (FakeAuth(raising=True), FakeConfig(), ["mcp_not_permitted", "mcp_not_permitted"]),
        (FakeAuth(), FakeConfig(valid=False), ["mcp_override", "mcp_override"]),
    ],
)
def test_mcp_context_refusals(auth: FakeAuth, config: FakeConfig, codes: list[str]) -> None:
    spec, policy = _record()
    reasons = _checks(auth, config).reasons(spec, policy, BlobDir(), CTX)
    assert [r.code for r in reasons] == codes
    if codes[0] == "mcp_override":
        assert "LOCAL" in reasons[0].message


def test_context_checks_wait_for_clean_entries() -> None:
    spec, policy = _record()
    spec.tools.mcp[0].config.headers = {"X-A": "1", "x-a": "2"}  # past the schema, not the Manifest rule
    auth = FakeAuth()
    reasons = _checks(auth).reasons(spec, policy, BlobDir(), CTX)
    assert [r.code for r in reasons] == ["mcp_entry"] and auth.calls == []


# -- memory and script -----------------------------------------------------------------------------


def test_memory_items_must_be_canonical_sorted_and_valid() -> None:
    blobs = BlobDir()
    spec, policy = _record()
    checks = _checks()
    spec.memory = MemorySpec(
        mode="seed",
        items_digest=blobs.put(
            b'[{"key":"b","source":[],"tags":[],"text":"t"},{"key":"a","source":[],"tags":[],"text":"t"}]'
        ),
    )
    assert [r.code for r in checks.reasons(spec, policy, blobs, CTX)] == ["order"]
    spec.memory.items_digest = blobs.put(b'[{"key":"A","text":"t"}]')
    assert {r.code for r in checks.reasons(spec, policy, blobs, CTX)} == {"schema"}
    spec.memory.items_digest = blobs.put(b"not json")
    assert [r.code for r in checks.reasons(spec, policy, blobs, CTX)] == ["schema"]


def test_script_is_bounded_like_the_startup_script_service() -> None:
    blobs = BlobDir()
    spec, policy = _record()
    spec.script = ScriptEntry(digest=blobs.put(b"#" * (MAX_SCRIPT_BYTES + 1)))
    assert [(r.code, r.location) for r in _checks().reasons(spec, policy, blobs, CTX)] == [
        ("limit_exceeded", "spec.script")
    ]
    spec.script = ScriptEntry(digest=blobs.put(b"#" * MAX_SCRIPT_BYTES))
    assert _checks().reasons(spec, policy, blobs, CTX) == []


def test_script_must_be_utf8() -> None:
    blobs = BlobDir()
    spec, policy = _record()
    spec.script = ScriptEntry(digest=blobs.put(b"\xff\xfe"))
    assert [(r.code, r.location) for r in _checks().reasons(spec, policy, blobs, CTX)] == [("not_utf8", "spec.script")]


def test_memory_item_tags_and_sources_must_be_sorted() -> None:
    blobs = BlobDir()
    spec, policy = _record()
    spec.memory = MemorySpec(
        mode="seed",
        items_digest=blobs.put(b'[{"key":"a","source":["feedback:f","episode:e"],"tags":["b","a"],"text":"t"}]'),
    )
    reasons = _checks().reasons(spec, policy, blobs, CTX)
    assert [(r.code, r.location) for r in reasons] == [
        ("order", "spec.memory.items_digest:a.tags"),
        ("order", "spec.memory.items_digest:a.source"),
    ]


def test_a_drive_letter_is_not_a_tree_member() -> None:
    reasons, _ = tree_reasons("here", b"acm-tree-v1\n6:C:/x.md1:x")
    assert reasons and reasons[0].code == "tree"
