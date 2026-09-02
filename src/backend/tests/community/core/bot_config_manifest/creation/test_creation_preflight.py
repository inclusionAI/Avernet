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

# A registry standing one materialiser short of today's. Written out rather than
# read from the service so this suite tests the gate, not the wiring — the
# derivation itself is pinned in the apply service's own tests, and a set read
# from the service would leave these cases with nothing to refuse the moment the
# last materialiser landed.
_LANDED = frozenset(
    {
        ManifestSection.SCRIPT,
        ManifestCategory.MCP,
        ManifestCategory.IDENTITY,
        ManifestCategory.SKILLS,
    }
)

#: The category held out of ``_LANDED`` above, and the example throughout this
#: suite. It was the real gap until W6 landed the resources materialiser; it is
#: a stand-in now, because every category the *capability* layer admits is
#: materialised today and the two that are not (``engine_config``,
#: ``cli_tools``) are refused a layer earlier, as ``unsupported_category``.
#: That makes this gate unreachable from the wire — which is why it is tested
#: here against a constructed registry rather than through the endpoint, and
#: why it stays: the vocabulary is expected to keep outrunning the code, and
#: the next category to do so meets this gate on the way in.
_UNMATERIALISED = (
    'schema_version: 1\nmanifest:\n  resources:\n'
    '    - path: "docs/a.md"\n      source: "https://example.com/a.md"\n'
)


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
    )


def test_a_document_declaring_only_landed_constructs_is_accepted():
    parsed = _preflight('schema_version: 1\nscript:\n  body: "echo hi"\n')
    assert parsed


def test_a_construct_with_no_materialiser_is_refused_at_submission():
    with pytest.raises(ManifestValidationError) as caught:
        _preflight(_UNMATERIALISED)
    codes = {v.code for v in caught.value.violations}
    assert "construct_not_appliable_at_creation" in codes
    named = " ".join(v.message for v in caught.value.violations)
    assert "resources" in named, "the refusal must name the construct"


def test_a_declared_empty_category_still_needs_a_materialiser():
    """`resources: []` is not "nothing to do".

    Under §3.2's category overwrite it *empties* the category, which is a write,
    and a write needs something able to make it.
    """
    with pytest.raises(ManifestValidationError) as caught:
        _preflight("schema_version: 1\nmanifest:\n  resources: []\n")
    assert "construct_not_appliable_at_creation" in {
        v.code for v in caught.value.violations
    }


def test_registering_the_materialiser_makes_the_same_document_acceptable():
    """The gate is derived, so landing a materialiser widens it and nothing else.

    W6 did exactly this for ``resources`` in the real registry; here it is done
    to the fixture, which is the same edit the service's own derivation makes.
    """
    with pytest.raises(ManifestValidationError):
        _preflight(_UNMATERIALISED)
    parsed = _preflight(
        _UNMATERIALISED, materialised=_LANDED | {ManifestCategory.RESOURCES}
    )
    assert parsed


def test_a_teclaw_engine_creates_like_any_other():
    """W8: the refusal is gone. What teclaw cannot deliver, the validator
    refuses per construct — not this preflight per engine."""
    parsed = _preflight('schema_version: 1\nmanifest:\n  mcp: []\n', engine="teclaw")
    assert parsed["manifest"] == {"mcp": []}
    assert "engine_not_supported_for_creation" not in str(
        preflight_creation_manifest.__doc__
    )


def test_script_on_teclaw_is_still_the_validators_refusal():
    with pytest.raises(ManifestValidationError) as caught:
        _preflight('schema_version: 1\nscript:\n  body: "echo hi"\n', engine="teclaw")
    codes = {v.code for v in caught.value.violations}
    assert codes == {"unsupported_script"}, codes


def test_every_reason_is_reported_in_one_pass():
    """All-or-nothing, like ``PUT``: fixing a document is one pass, not a queue."""
    with pytest.raises(ManifestValidationError) as caught:
        _preflight(_UNMATERIALISED, engine="teclaw")
    codes = {v.code for v in caught.value.violations}
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
