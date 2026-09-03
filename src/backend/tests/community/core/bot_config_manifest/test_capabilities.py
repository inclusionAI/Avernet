"""The capability resolver — one function, two entry points, no third state.

These are the properties the rest of the feature leans on: ``/capabilities`` and
``PUT`` cannot disagree because they call this; W13 can call it before a bot
record exists; and every construct the vocabulary can express has a verdict.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.capabilities import (
    ConstructKind,
    ManifestCategory,
    ManifestSection,
    SourceForm,
    capabilities_for_bot,
    kind_of,
    resolve_capabilities,
)


def _is_teclaw(engine: str | None) -> bool:
    return (engine or "").strip().lower() == "teclaw"


def _resolve(engine="openclaw", bot_type="personal"):
    return resolve_capabilities(
        active_engine=engine, bot_type=bot_type, is_teclaw=_is_teclaw
    )


def test_every_construct_in_the_vocabulary_has_a_verdict():
    """A construct with no row is a construct nobody ruled on.

    Derived from the enums rather than from a second list here, so a construct
    added to the vocabulary without a verdict fails this instead of quietly
    reading as unsupported.
    """
    caps = _resolve()
    ruled_on = {c.construct for c in caps.constructs}
    expected = {*ManifestCategory, *ManifestSection, *SourceForm}
    assert ruled_on == expected


def test_a_constructs_kind_is_its_type_not_a_second_field():
    """``kind`` and ``name`` are correlated, so ``kind`` is derived. There is no
    way to build a ``source`` called ``mcp``."""
    caps = _resolve()
    for capability in caps.constructs:
        assert capability.kind is kind_of(capability.construct)
    assert kind_of(ManifestCategory.MCP) is ConstructKind.CATEGORY
    assert kind_of(ManifestSection.SCRIPT) is ConstructKind.SECTION
    assert kind_of(SourceForm.GIT) is ConstructKind.SOURCE


def test_an_unsupported_construct_always_carries_a_reason():
    caps = _resolve()
    for construct in caps.constructs:
        assert bool(construct.reason) is not construct.supported


@pytest.mark.parametrize(
    "category",
    [
        ManifestCategory.MCP,
        ManifestCategory.RESOURCES,
        ManifestCategory.SKILLS,
        ManifestCategory.IDENTITY,
        # W9: materialised through ``CliToolService`` — the one component the
        # management API installs through too.
        ManifestCategory.CLI_TOOLS,
    ],
)
def test_the_first_wave_categories_are_supported(category):
    assert _resolve().supports(category)


@pytest.mark.parametrize("category", [ManifestCategory.ENGINE_CONFIG])
def test_the_categories_with_no_materializer_are_refused(category):
    """What is left of the first-wave table: expressible, nothing applies it."""
    caps = _resolve()
    assert not caps.supports(category)
    assert caps.reason_for(category)


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "teclaw"])
def test_cli_tools_is_supported_on_both_engine_families(engine):
    """ARCA installs into the live container, teclaw carries the refs in its
    artifact. Both are delivered, so both are supported."""
    assert _resolve(engine=engine).supports(ManifestCategory.CLI_TOOLS)


def test_cli_tools_is_refused_on_a_desktop_bot():
    """The deployment-wide refusal still wins over the per-construct one."""
    caps = _resolve(bot_type="desktop")
    assert not caps.supports(ManifestCategory.CLI_TOOLS)
    assert "desktop" in caps.reason_for(ManifestCategory.CLI_TOOLS)


def test_cli_tools_is_refused_on_an_unknown_engine():
    caps = _resolve(engine="not-an-engine")
    assert not caps.supports(ManifestCategory.CLI_TOOLS)
    assert caps.reason_for(ManifestCategory.CLI_TOOLS)


@pytest.mark.parametrize("source", [SourceForm.GIT, SourceForm.NAMED])
def test_the_w7_source_forms_are_resolved(source):
    """The point of answering per *construct* rather than per category.

    A source form with no resolver fails exactly the way an unsupported
    category does, and W7 delivered resolvers for these two — the gate flip
    this file pinned the other way around before the delivery was reachable.
    The one (category, form) pair still undelivered — resources entries
    naming git or named sources — is refused per entry by the schema, which
    is where a category-aware reason can live.
    """
    caps = _resolve()
    assert caps.supports(source)
    assert caps.reason_for(source) == ""


def test_script_is_refused_for_a_teclaw_bot():
    caps = _resolve(engine="teclaw")
    assert not caps.supports(ManifestSection.SCRIPT)
    assert "teclaw" in caps.reason_for(ManifestSection.SCRIPT)


def test_teclaw_still_supports_the_declarative_categories():
    """Only ``script`` is engine-gated; teclaw takes the manifest itself."""
    caps = _resolve(engine="teclaw")
    assert caps.supports(ManifestCategory.IDENTITY)
    assert caps.supports(ManifestCategory.SKILLS)


def test_a_desktop_bot_supports_nothing():
    """Desktop is out of this feature's scope, so the check only ever refuses."""
    caps = _resolve(bot_type="desktop")
    assert all(not c.supported for c in caps.constructs)


def test_an_unknown_engine_supports_nothing():
    caps = _resolve(engine="not-an-engine")
    assert all(not c.supported for c in caps.constructs)
    assert "engine" in caps.reason_for(ManifestCategory.IDENTITY)


def test_a_missing_engine_reads_as_the_platform_default_not_as_unknown():
    """A bot mid-creation has no engine yet; that is not the same as a bad one."""
    caps = resolve_capabilities(
        active_engine=None, bot_type="personal", is_teclaw=_is_teclaw
    )
    assert caps.supports(ManifestCategory.IDENTITY)


def test_a_construct_string_from_a_document_is_parsed_not_trusted():
    """A submitted document may name anything; only the enum crosses inward."""
    from agentclaw.community.core.bot_config_manifest.capabilities import (
        parse_category,
    )

    assert parse_category("mcp") is ManifestCategory.MCP
    assert parse_category("telepathy") is None
    assert parse_category(7) is None


def test_the_bot_entry_point_is_the_same_answer_as_the_record_free_one():
    """The acceptance criterion: one function, two entry points.

    W13 validates before an ``ac_bots`` row exists, so the record-free call has
    to be the same body — not a second implementation that drifts.
    """
    bot = {"active_engine": "teclaw", "bot_type": "personal", "bot_id": "b1"}
    from_record = capabilities_for_bot(bot, _is_teclaw)
    from_fields = resolve_capabilities(
        active_engine="teclaw", bot_type="personal", is_teclaw=_is_teclaw
    )
    assert from_record == from_fields


def test_the_payload_is_flat_and_names_what_it_answered_for():
    """The wire shape is unchanged by the enums: a construct still serialises as
    a ``kind``/``name`` pair of plain strings."""
    payload = _resolve().as_payload()
    assert payload["engine_type"] == "openclaw"
    assert payload["bot_type"] == "personal"
    assert payload["schema_versions"] == [1]
    first = payload["constructs"][0]
    assert {"kind", "name", "supported", "reason"} == set(first)
    assert first["kind"] == "category" and first["name"] == "mcp"
