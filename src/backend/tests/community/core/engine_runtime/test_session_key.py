"""Session-key codecs — the engine-specific wire form of a session id.

The registry is the whole point of the module: the rule that teclaw's runtime
refuses a colon-bearing path segment lives in one registration instead of an
``if`` at every handler that builds a session path. These tests pin both
halves — that an unregistered engine is untouched, and that the teclaw form is
exactly what the engine's own ``decode_session_key`` reverses.
"""

from __future__ import annotations

import base64

import pytest

from agentclaw.community.core.engine_runtime.session_key import (
    Base64SessionKeyCodec,
    PassThroughSessionKeyCodec,
    SessionKeyCodec,
    SessionKeyCodecRegistry,
    encode_session_key,
    get_session_key_codec_registry,
)

#: The id from the failing production request, verbatim.
TECLAW_SESSION_ID = "agent:default:default:c_01M2F998Z1BQ9VDKVQ52N9RXQP:user:272471"


def _decode(session_id: str) -> str:
    """``engine.community.shared.utils.decode_session_key``, restated.

    The engine is a separate package and not importable from the backend's
    tests, so its decoder is mirrored here — an encoding the engine cannot
    reverse is the one failure this module must never ship.
    """
    padding = 4 - len(session_id) % 4
    if padding != 4:
        session_id += "=" * padding
    return base64.urlsafe_b64decode(session_id).decode()


# ── the default ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("engine", ["openclaw", "claude_code", "hermes", "aicoding"])
def test_an_engine_without_a_codec_gets_the_session_id_verbatim(engine):
    assert encode_session_key(engine, TECLAW_SESSION_ID) == TECLAW_SESSION_ID


@pytest.mark.parametrize("engine", [None, "", "   ", "an_engine_we_never_shipped"])
def test_an_unknown_engine_falls_back_to_pass_through(engine):
    assert encode_session_key(engine, TECLAW_SESSION_ID) == TECLAW_SESSION_ID


# ── teclaw ────────────────────────────────────────────────────────────────────


def test_teclaw_session_ids_travel_base64_encoded():
    encoded = encode_session_key("teclaw", TECLAW_SESSION_ID)

    assert encoded != TECLAW_SESSION_ID
    assert ":" not in encoded


def test_the_teclaw_form_is_what_the_engine_decodes_back():
    encoded = encode_session_key("teclaw", TECLAW_SESSION_ID)

    assert _decode(encoded) == TECLAW_SESSION_ID


def test_the_teclaw_form_is_a_single_path_segment():
    """URL-safe alphabet, unpadded: no "/" to split the segment, nothing to quote."""
    encoded = encode_session_key("teclaw", TECLAW_SESSION_ID)

    assert "/" not in encoded
    assert "+" not in encoded
    assert not encoded.endswith("=")


@pytest.mark.parametrize("spelling", ["teclaw", "TECLAW", "  TeClaw  "])
def test_the_engine_spelling_is_normalised_before_lookup(spelling):
    assert encode_session_key(spelling, TECLAW_SESSION_ID) == encode_session_key(
        "teclaw", TECLAW_SESSION_ID
    )


# ── the registry ──────────────────────────────────────────────────────────────


def test_registering_an_engine_overrides_the_default_for_it_alone():
    class _Upper:
        def encode(self, session_key: str) -> str:
            return session_key.upper()

    registry = SessionKeyCodecRegistry()
    registry.register("shouty", _Upper())

    assert registry.resolve("shouty").encode("abc") == "ABC"
    assert registry.resolve("openclaw").encode("abc") == "abc"


def test_a_double_registration_is_refused_rather_than_silently_overwriting():
    registry = SessionKeyCodecRegistry()
    registry.register("teclaw", Base64SessionKeyCodec())

    with pytest.raises(ValueError, match="already registered"):
        registry.register("teclaw", PassThroughSessionKeyCodec())


def test_the_process_wide_registry_is_one_instance():
    assert get_session_key_codec_registry() is get_session_key_codec_registry()


def test_the_bundled_codecs_satisfy_the_protocol():
    assert isinstance(PassThroughSessionKeyCodec(), SessionKeyCodec)
    assert isinstance(Base64SessionKeyCodec(), SessionKeyCodec)
