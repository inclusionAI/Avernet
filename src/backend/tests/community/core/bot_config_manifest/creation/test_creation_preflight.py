"""The creation preflight refuses what this path could not actually deliver.

Deliberately stricter than ``PUT``, and the difference is the point: ``PUT`` may
accept a category no materialiser can act on — the document sits inert and
nothing was created — while accepting one here spends a Passport application,
a user's authorization click and a live bot before the failure appears.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    ManifestSection,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    declared_constructs,
    preflight_creation_manifest,
)
from agentclaw.community.core.bot_config_manifest.schema import (
    ManifestValidationError,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    resolve_capabilities,
)
from agentclaw.community.core.bot_config_manifest.schema.validator import (
    validate_document,
)

_LANDED = frozenset({ManifestSection.SCRIPT, ManifestCategory.MCP})


def _validate(*, document, active_engine, bot_type):
    """The service-level wrapper's shape: resolve capabilities, then validate."""
    return validate_document(
        document,
        resolve_capabilities(
            active_engine=active_engine,
            bot_type=bot_type,
            is_teclaw=lambda engine: engine == "teclaw",
        ),
    )


def _preflight(document, *, engine="claude_code", materialised=_LANDED):
    return preflight_creation_manifest(
        document=document,
        engine_type=engine,
        bot_type="personal",
        validate=_validate,
        materialised=materialised,
        is_teclaw=lambda e: e == "teclaw",
    )


def test_a_document_declaring_only_landed_constructs_is_accepted():
    parsed = _preflight('schema_version: 1\nscript:\n  body: "echo hi"\n')
    assert parsed


def test_a_construct_with_no_materialiser_is_refused_at_submission():
    with pytest.raises(ManifestValidationError) as caught:
        _preflight(
            "schema_version: 1\nmanifest:\n  identity:\n"
            '    - type: "CLAUDE.md"\n      content: "hello"\n'
        )
    codes = {v.code for v in caught.value.violations}
    assert "construct_not_appliable_at_creation" in codes
    named = " ".join(v.message for v in caught.value.violations)
    assert "identity" in named, "the refusal must name the construct"


def test_a_declared_empty_category_still_needs_a_materialiser():
    """`identity: []` is not "nothing to do".

    Under §3.2's category overwrite it *empties* the category, which is a write,
    and a write needs something able to make it.
    """
    with pytest.raises(ManifestValidationError) as caught:
        _preflight("schema_version: 1\nmanifest:\n  identity: []\n")
    assert "construct_not_appliable_at_creation" in {
        v.code for v in caught.value.violations
    }


def test_registering_the_materialiser_makes_the_same_document_acceptable():
    """The gate is derived, so W5/W6 widen it by landing."""
    document = (
        "schema_version: 1\nmanifest:\n  identity:\n"
        '    - type: "CLAUDE.md"\n      content: "hello"\n'
    )
    with pytest.raises(ManifestValidationError):
        _preflight(document)
    parsed = _preflight(
        document, materialised=_LANDED | {ManifestCategory.IDENTITY}
    )
    assert parsed


def test_a_teclaw_engine_is_refused_and_the_refusal_names_w8():
    with pytest.raises(ManifestValidationError) as caught:
        _preflight('schema_version: 1\nmanifest:\n  mcp: []\n', engine="teclaw")
    violation = next(
        v for v in caught.value.violations if v.location == "engine"
    )
    assert violation.code == "engine_not_supported_for_creation"
    assert "W8" in violation.message


def test_every_reason_is_reported_in_one_pass():
    """All-or-nothing, like ``PUT``: fixing a document is one pass, not a queue."""
    with pytest.raises(ManifestValidationError) as caught:
        _preflight(
            "schema_version: 1\nmanifest:\n  identity:\n"
            '    - type: "CLAUDE.md"\n      content: "hi"\n',
            engine="teclaw",
        )
    codes = {v.code for v in caught.value.violations}
    assert "engine_not_supported_for_creation" in codes
    assert "construct_not_appliable_at_creation" in codes


def test_declared_constructs_follows_apply_order():
    parsed = _validate(
        document=(
            'schema_version: 1\nscript:\n  body: "echo hi"\n'
            "manifest:\n  mcp: []\n"
        ),
        active_engine="claude_code",
        bot_type="personal",
    ).parsed
    names = [c.value for c in declared_constructs(parsed)]
    assert names == ["script", "mcp"], names
