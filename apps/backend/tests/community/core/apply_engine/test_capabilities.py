"""The capability resolver — one function, two entry points, no third state.

These are the properties the rest of the feature leans on: ``/capabilities`` and
``PUT`` cannot disagree because they call this; W13 can call it before a bot
record exists; and every construct the vocabulary can express has a verdict.
"""
from __future__ import annotations

import pytest

import agentclaw.community.core.apply_engine.capabilities as capabilities_module

from agentclaw.community.core.apply_engine.capabilities import (
    ConstructKind,
    Category,
    Section,
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
    expected = {*Category, *Section}
    assert ruled_on == expected


def test_a_constructs_kind_is_its_type_not_a_second_field():
    """``kind`` and ``name`` are correlated, so ``kind`` is derived. There is no
    way to build a ``source`` called ``mcp``."""
    caps = _resolve()
    for capability in caps.constructs:
        assert capability.kind is kind_of(capability.construct)
    assert kind_of(Category.MCP) is ConstructKind.CATEGORY
    assert kind_of(Section.SCRIPT) is ConstructKind.SECTION


def test_an_unsupported_construct_always_carries_a_reason():
    caps = _resolve()
    for construct in caps.constructs:
        assert bool(construct.reason) is not construct.supported


@pytest.mark.parametrize(
    "category",
    [
        Category.MCP,
        Category.RESOURCES,
        Category.SKILLS,
        Category.IDENTITY,
        # W9: materialised through ``CliToolService`` — the one component the
        # management API installs through too.
        Category.CLI_TOOLS,
    ],
)
def test_the_first_wave_categories_are_supported(category):
    assert _resolve().supports(category)


@pytest.mark.parametrize("category", [Category.ENGINE_CONFIG])
def test_the_categories_with_no_materializer_are_refused(category):
    """What is left of the first-wave table: expressible, nothing applies it."""
    caps = _resolve()
    assert not caps.supports(category)
    assert caps.reason_for(category)


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "teclaw"])
def test_cli_tools_is_supported_on_both_engine_families(engine):
    """ARCA installs into the live container, teclaw carries the refs in its
    artifact. Both are delivered, so both are supported."""
    assert _resolve(engine=engine).supports(Category.CLI_TOOLS)


def test_cli_tools_is_refused_on_a_desktop_bot():
    """The deployment-wide refusal still wins over the per-construct one."""
    caps = _resolve(bot_type="desktop")
    assert not caps.supports(Category.CLI_TOOLS)
    assert "desktop" in caps.reason_for(Category.CLI_TOOLS)


def test_cli_tools_is_refused_on_an_unknown_engine():
    caps = _resolve(engine="not-an-engine")
    assert not caps.supports(Category.CLI_TOOLS)
    assert caps.reason_for(Category.CLI_TOOLS)


def test_no_construct_is_published_for_a_source_spelling():
    """The ``source`` construct kind is gone, and this pins that it stays gone.

    It published one row per spelling (``oss``/``git``/``named``/``content``),
    and review asked what ``named`` was doing there. The answer, measured
    across every engine/bot-type/teclaw configuration: no spelling ever
    carried a verdict of its own — the only refusal is bot-wide and hits all
    four identically, and it is already reported per category. Four rows that
    could never disagree, plus a per-entry violation restating the category's
    own refusal in the same words.

    Which ``(category, protocol)`` pairs are open is the question that was
    actually being asked, and ``source_matrix`` answers it on ``SourceKind``.
    """
    caps = _resolve()
    assert {c.kind for c in caps.constructs} == {
        ConstructKind.CATEGORY,
        ConstructKind.SECTION,
    }
    assert not hasattr(capabilities_module, "SourceForm")


def test_script_is_refused_for_a_teclaw_bot():
    caps = _resolve(engine="teclaw")
    assert not caps.supports(Section.SCRIPT)
    assert "teclaw" in caps.reason_for(Section.SCRIPT)


def test_teclaw_still_supports_the_declarative_categories():
    """Only ``script`` is engine-gated; teclaw takes the manifest itself."""
    caps = _resolve(engine="teclaw")
    assert caps.supports(Category.IDENTITY)
    assert caps.supports(Category.SKILLS)


def test_a_desktop_bot_supports_nothing():
    """Desktop is out of this feature's scope, so the check only ever refuses."""
    caps = _resolve(bot_type="desktop")
    assert all(not c.supported for c in caps.constructs)


def test_an_unknown_engine_supports_nothing():
    caps = _resolve(engine="not-an-engine")
    assert all(not c.supported for c in caps.constructs)
    assert "engine" in caps.reason_for(Category.IDENTITY)


def test_a_missing_engine_reads_as_the_platform_default_not_as_unknown():
    """A bot mid-creation has no engine yet; that is not the same as a bad one."""
    caps = resolve_capabilities(
        active_engine=None, bot_type="personal", is_teclaw=_is_teclaw
    )
    assert caps.supports(Category.IDENTITY)


def test_a_construct_string_from_a_document_is_parsed_not_trusted():
    """A submitted document may name anything; only the enum crosses inward."""
    from agentclaw.community.core.apply_engine.capabilities import (
        parse_category,
    )

    assert parse_category("mcp") is Category.MCP
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
