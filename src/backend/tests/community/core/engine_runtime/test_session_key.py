"""Session-key codecs — the engine-specific wire form of a session id.

The registry is the whole point of the module: the rule that teclaw's runtime
refuses a colon-bearing path segment lives in one registration instead of an
``if`` at every handler that builds a session path. These tests pin both
halves — that an engine nobody registered is untouched, and that the teclaw
form is exactly what the engine's own ``decode_session_key`` reverses — plus
the two things the registry refuses rather than guesses.
"""

from __future__ import annotations

import base64

import pytest

from agentclaw.community.core.engine_runtime.session_key import (
    TECLAW_ENGINE_TYPE,
    Base64SessionKeyCodec,
    PassThroughSessionKeyCodec,
    SessionKeyCodec,
    SessionKeyCodecRegistry,
)

#: The id from the failing production request, verbatim.
TECLAW_SESSION_ID = "agent:default:default:c_01M2F998Z1BQ9VDKVQ52N9RXQP:user:272471"


@pytest.fixture
def registry() -> SessionKeyCodecRegistry:
    """The registry as the composition root assembles it."""
    built = SessionKeyCodecRegistry(PassThroughSessionKeyCodec())
    built.register(TECLAW_ENGINE_TYPE, Base64SessionKeyCodec())
    return built


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
def test_an_engine_without_a_codec_gets_the_session_id_verbatim(registry, engine):
    assert registry.resolve(engine).encode(TECLAW_SESSION_ID) == TECLAW_SESSION_ID


def test_an_engine_nobody_ever_shipped_still_falls_back_to_the_default(registry):
    encoded = registry.resolve("an_engine_we_never_shipped").encode(TECLAW_SESSION_ID)

    assert encoded == TECLAW_SESSION_ID


# ── teclaw ────────────────────────────────────────────────────────────────────


def test_teclaw_session_ids_travel_base64_encoded(registry):
    encoded = registry.resolve(TECLAW_ENGINE_TYPE).encode(TECLAW_SESSION_ID)

    assert encoded != TECLAW_SESSION_ID
    assert ":" not in encoded


def test_the_teclaw_form_is_what_the_engine_decodes_back(registry):
    encoded = registry.resolve(TECLAW_ENGINE_TYPE).encode(TECLAW_SESSION_ID)

    assert _decode(encoded) == TECLAW_SESSION_ID


def test_the_teclaw_form_is_a_single_path_segment(registry):
    """URL-safe alphabet, unpadded: no "/" to split the segment, nothing to quote."""
    encoded = registry.resolve(TECLAW_ENGINE_TYPE).encode(TECLAW_SESSION_ID)

    assert "/" not in encoded
    assert "+" not in encoded
    assert not encoded.endswith("=")


@pytest.mark.parametrize("spelling", ["teclaw", "TECLAW", "  TeClaw  "])
def test_the_engine_spelling_is_normalised_before_lookup(registry, spelling):
    assert registry.resolve(spelling).encode(TECLAW_SESSION_ID) == registry.resolve(
        TECLAW_ENGINE_TYPE
    ).encode(TECLAW_SESSION_ID)


# ── what the registry refuses ────────────────────────────────────────────────


@pytest.mark.parametrize("engine", ["", "   "])
def test_resolving_without_an_engine_is_an_error_not_a_default(registry, engine):
    """``ac_bots.active_engine`` is NOT NULL with a default, so a blank one is
    corrupt data — picking a wire format for it would forward on the strength
    of a value nobody wrote."""
    with pytest.raises(ValueError, match="engine type is required"):
        registry.resolve(engine)


@pytest.mark.parametrize("engine", ["", "   "])
def test_registering_without_an_engine_is_an_error_not_a_default(registry, engine):
    with pytest.raises(ValueError, match="engine type is required"):
        registry.register(engine, Base64SessionKeyCodec())


def test_a_double_registration_is_refused_rather_than_silently_overwriting(registry):
    with pytest.raises(ValueError, match="already registered"):
        registry.register(TECLAW_ENGINE_TYPE, PassThroughSessionKeyCodec())


def test_registering_an_engine_overrides_the_default_for_it_alone():
    class _Upper(SessionKeyCodec):
        def encode(self, session_key: str) -> str:
            return session_key.upper()

    built = SessionKeyCodecRegistry(PassThroughSessionKeyCodec())
    built.register("shouty", _Upper())

    assert built.resolve("shouty").encode("abc") == "ABC"
    assert built.resolve("openclaw").encode("abc") == "abc"


# ── the contract itself ──────────────────────────────────────────────────────


def test_the_bundled_codecs_inherit_the_protocol():
    """Inheritance, not duck typing: the contract has one declaration.

    Asserted on the MRO rather than with ``issubclass``, which a Protocol
    answers only when it is ``@runtime_checkable`` — and that decorator is the
    structural-matching escape hatch this contract exists to close. A test that
    needed it would be the only thing keeping it alive.
    """
    assert SessionKeyCodec in PassThroughSessionKeyCodec.__mro__
    assert SessionKeyCodec in Base64SessionKeyCodec.__mro__


def test_a_look_alike_class_is_not_a_codec():
    """The contract admits nominal subclasses only."""

    class _Duck:
        def encode(self, session_key: str) -> str:
            return session_key

    assert SessionKeyCodec not in _Duck.__mro__


def test_a_codec_that_never_implements_encode_cannot_be_constructed():
    """The abstract method is what makes the inheritance load-bearing."""

    class _Hollow(SessionKeyCodec):
        pass

    with pytest.raises(TypeError, match="abstract"):
        _Hollow()
