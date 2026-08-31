"""The capability resolver — one function, two entry points, no third state.

These are the properties the rest of the feature leans on: ``/capabilities`` and
``PUT`` cannot disagree because they call this; W13 can call it before a bot
record exists; and every construct the vocabulary can express has a verdict.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.capabilities import (
    CATEGORIES,
    KIND_CATEGORY,
    KIND_SECTION,
    KIND_SOURCE,
    SECTION_SCRIPT,
    SOURCE_CONTENT,
    SOURCE_GIT,
    SOURCE_NAMED,
    SOURCE_URL,
    capabilities_for_bot,
    resolve_capabilities,
)


def _is_teclaw(engine: str | None) -> bool:
    return (engine or "").strip().lower() == "teclaw"


def _resolve(engine="openclaw", bot_type="personal"):
    return resolve_capabilities(
        active_engine=engine, bot_type=bot_type, is_teclaw=_is_teclaw
    )


def test_every_category_and_section_and_source_has_a_verdict():
    """A construct with no row is a construct nobody ruled on."""
    caps = _resolve()
    named = {(c.kind, c.name) for c in caps.constructs}
    for category in CATEGORIES:
        assert (KIND_CATEGORY, category) in named
    assert (KIND_SECTION, SECTION_SCRIPT) in named
    for source in (SOURCE_URL, SOURCE_GIT, SOURCE_NAMED, SOURCE_CONTENT):
        assert (KIND_SOURCE, source) in named


def test_an_unsupported_construct_always_carries_a_reason():
    caps = _resolve()
    for construct in caps.constructs:
        assert bool(construct.reason) is not construct.supported


@pytest.mark.parametrize("category", ["mcp", "resources", "skills", "identity"])
def test_the_first_wave_categories_are_supported(category):
    assert _resolve().supports(KIND_CATEGORY, category)


@pytest.mark.parametrize("category", ["cli_tools", "engine_config"])
def test_the_categories_with_no_materializer_are_refused(category):
    """Both rows of the first-wave table: expressible, and nothing applies them."""
    caps = _resolve()
    assert not caps.supports(KIND_CATEGORY, category)
    assert caps.reason_for(KIND_CATEGORY, category)


@pytest.mark.parametrize("source", [SOURCE_GIT, SOURCE_NAMED])
def test_the_source_forms_with_no_resolver_are_refused(source):
    """The point of answering per *construct* rather than per category.

    A source form with no resolver fails exactly the way an unsupported category
    does, so it is refused the same way.
    """
    assert not _resolve().supports(KIND_SOURCE, source)


def test_script_is_refused_for_a_teclaw_bot():
    caps = _resolve(engine="teclaw")
    assert not caps.supports(KIND_SECTION, SECTION_SCRIPT)
    assert "teclaw" in caps.reason_for(KIND_SECTION, SECTION_SCRIPT)


def test_teclaw_still_supports_the_declarative_categories():
    """Only ``script`` is engine-gated; teclaw takes the manifest itself."""
    caps = _resolve(engine="teclaw")
    assert caps.supports(KIND_CATEGORY, "identity")
    assert caps.supports(KIND_CATEGORY, "skills")


def test_a_desktop_bot_supports_nothing():
    """Desktop is out of this feature's scope, so the check only ever refuses."""
    caps = _resolve(bot_type="desktop")
    assert all(not c.supported for c in caps.constructs)


def test_an_unknown_engine_supports_nothing():
    caps = _resolve(engine="not-an-engine")
    assert all(not c.supported for c in caps.constructs)
    assert "engine" in caps.reason_for(KIND_CATEGORY, "identity")


def test_a_missing_engine_reads_as_the_platform_default_not_as_unknown():
    """A bot mid-creation has no engine yet; that is not the same as a bad one."""
    caps = resolve_capabilities(
        active_engine=None, bot_type="personal", is_teclaw=_is_teclaw
    )
    assert caps.supports(KIND_CATEGORY, "identity")


def test_an_unrecognised_construct_is_not_supported():
    """The conservative default: a name nobody ruled on is a name nothing applies."""
    caps = _resolve()
    assert not caps.supports(KIND_CATEGORY, "telepathy")
    assert "unknown" in caps.reason_for(KIND_CATEGORY, "telepathy")


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
    payload = _resolve().as_payload()
    assert payload["engine_type"] == "openclaw"
    assert payload["bot_type"] == "personal"
    assert payload["schema_versions"] == [1]
    assert {"kind", "name", "supported", "reason"} == set(payload["constructs"][0])
