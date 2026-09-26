"""Judgment matrix for the engine/form vocabulary split.

``uses_aicoding_runtime`` is the single judgment entry for "does this bot run
the AICoding runtime implementation" (see
docs/superpowers/specs/2026-08-31-engine-vocabulary-template-form-design.md):
a stored ``aicoding`` engine short-circuits (read paths never rewrite stored
engines), the server-managed ``engine_form`` marker routes folded
``claude_code`` bots, and the historical template-type semantics stay intact.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.workspace.runtime_identity import (
    AICODING_ENGINE_FORM,
    ENGINE_FORM_KEY,
    claude_code_uses_aicoding_runtime,
    engine_form_of,
    uses_aicoding_runtime,
)

pytestmark = pytest.mark.unit


def test_stored_aicoding_engine_short_circuits_to_true():
    # Legacy rows read True regardless of template shape — the read path never
    # rewrites stored engines.
    assert uses_aicoding_runtime(
        active_engine="aicoding", template_type=None, template_config=None
    )
    assert uses_aicoding_runtime(
        active_engine="aicoding", template_type="normalCC", template_config={}
    )


def test_engine_form_marker_routes_folded_claude_code_bot():
    assert uses_aicoding_runtime(
        active_engine="claude_code",
        template_type="normalCC",
        template_config={ENGINE_FORM_KEY: AICODING_ENGINE_FORM},
    )
    # A plain (no-template-config) claude_code bot has no form.
    assert not uses_aicoding_runtime(
        active_engine="claude_code", template_type=None, template_config=None
    )


def test_marker_beats_template_type_semantics():
    # normalCC alone stays on the claude_code runtime; the marker says the bot
    # was folded from the legacy aicoding engine and must keep the aicoding
    # runtime — the marker is authoritative where both signals exist.
    assert not uses_aicoding_runtime(
        active_engine="claude_code", template_type="normalCC"
    )
    assert uses_aicoding_runtime(
        active_engine="claude_code",
        template_type="normalCC",
        template_config={ENGINE_FORM_KEY: AICODING_ENGINE_FORM},
    )


@pytest.mark.parametrize(
    "engine", ["openclaw", "teclaw", "hermes", "moltis", "", None]
)
def test_non_coding_engines_never_use_aicoding_runtime(engine):
    assert not uses_aicoding_runtime(
        active_engine=engine, template_type="applicationCoding"
    )
    assert not uses_aicoding_runtime(
        active_engine=engine,
        template_type="normalCC",
        template_config={ENGINE_FORM_KEY: AICODING_ENGINE_FORM},
    )


def test_historical_template_type_semantics_preserved():
    assert uses_aicoding_runtime(
        active_engine="claude_code", template_type="applicationCoding"
    )
    assert uses_aicoding_runtime(
        active_engine="claude_code", template_type="architect"
    )
    assert not uses_aicoding_runtime(
        active_engine="claude_code", template_type="normalCC"
    )
    # Engine spelling normalizes (registry key form).
    assert uses_aicoding_runtime(
        active_engine="claude-code", template_type="applicationCoding"
    )


def test_legacy_helper_keeps_its_historical_contract():
    # claude_code_uses_aicoding_runtime is the pre-split judgment: it must not
    # short-circuit on a stored aicoding engine (that input belongs to callers
    # that route by stored engine already).
    assert not claude_code_uses_aicoding_runtime(
        active_engine="aicoding", template_type="applicationCoding"
    )
    assert claude_code_uses_aicoding_runtime(
        active_engine="claude_code", template_type="applicationCoding"
    )
    assert not claude_code_uses_aicoding_runtime(
        active_engine="claude_code", template_type="normalCC"
    )


def test_engine_form_of_probes_sources_in_order():
    marker = {ENGINE_FORM_KEY: AICODING_ENGINE_FORM}
    assert (
        engine_form_of(marker, {ENGINE_FORM_KEY: "other"}) == AICODING_ENGINE_FORM
    )
    assert engine_form_of(None, marker) == AICODING_ENGINE_FORM
    assert engine_form_of(None, {"unrelated": 1}) is None
    assert engine_form_of({"template_config": marker}) is None  # nested ≠ marker


def test_engine_form_of_skips_non_string_and_blank_values():
    assert engine_form_of({ENGINE_FORM_KEY: 42}) is None
    assert engine_form_of({ENGINE_FORM_KEY: "   "}) is None
    assert engine_form_of({ENGINE_FORM_KEY: " aicoding "}) == AICODING_ENGINE_FORM
