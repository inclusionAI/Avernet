"""Unit tests for ``APIKeyGenerator`` — the app credential scheme.

This module is a verbatim copy of the secbaas API-gateway key generator, because
secbaas's existing ``baas_api_key`` records are migrated into
``avernet_application`` and must keep verifying with their original plaintext
keys. The tests here exist to pin that compatibility from both ends:

* :func:`test_secbaas_produced_hash_verifies` proves we can *read* what secbaas
  wrote — it asserts against a ``(key, hash)`` pair produced by running the real
  secbaas implementation.
* :func:`test_hash_key_output_uses_documented_pbkdf2_parameters` proves we
  *write* what secbaas would — the read-side test alone cannot catch a weakened
  salt or iteration count in this copy.

Neither is recoverable from the stored string, which carries only the salt: the
digest, the iteration count, and the encoding are implicit constants, so drift
in any of them silently invalidates every migrated record.

Two tests here characterize known upstream quirks rather than desired behavior
(see :func:`test_validate_format_accepts_a_trailing_newline` and
:func:`test_verify_tolerates_non_base64_noise_in_stored_hash`). Correcting them
means editing secbaas's file first and re-copying, since
:func:`test_copy_is_byte_identical_to_secbaas_source` forbids one-sided edits.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

from gateway.community.core.app import APIKeyGenerator

# Produced by running src/baas/.../api_gateway/_key_gen.py — do NOT regenerate
# these with the gateway's own copy, which would make the check circular.
_SECBAAS_KEY = "5X1tk2yC6rxmKhUfWzN2GJ3CYiGGE22F"
_SECBAAS_HASH = (
    "YIrLEzbZybtDzATwCkQ9QERLnn0Q9z09iO+u02jvGGs="
    ":UKS+A02LiRqVNsn0oOs9EiNO63ggsbZ3UHGnND6A08Q="
)

_SECBAAS_RELPATH = "src/baas/src/secbaas/community/core/service/api_gateway/_key_gen.py"
_OURS_RELPATH = "src/gateway/src/gateway/community/core/app/_key_gen.py"


def _repo_root() -> Path:
    """Walk up to the directory holding ``AGENTS.md``.

    Deliberately not a hardcoded ``parents[N]``: moving this file would silently
    change which path it resolves to, and the tests below would then skip rather
    than fail — quietly dropping the drift protection they exist to provide.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "AGENTS.md").is_file():
            return parent
    raise RuntimeError("cannot locate repository root: no AGENTS.md above this file")


def _load_secbaas_generator(source: Path) -> ModuleType:
    """Load the secbaas generator straight from disk.

    By file path rather than by package import because secbaas is deliberately
    *not* a dependency of ``src/gateway/pyproject.toml`` — the plan rejects the
    cross-module dependency and copies the class instead. The generator is
    stdlib-only, so the file loads cleanly on its own.
    """
    spec = importlib.util.spec_from_file_location("_secbaas_key_gen", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _secbaas_source() -> Path:
    source = _repo_root() / _SECBAAS_RELPATH
    assert source.is_file(), (
        f"secbaas key generator not found at {source}. This copy's migration "
        "compatibility is pinned against that file; if it moved, update "
        "_SECBAAS_RELPATH rather than letting these checks lapse."
    )
    return source


def test_secbaas_produced_hash_verifies() -> None:
    """The migration guarantee: a secbaas-hashed key verifies under our copy."""
    assert APIKeyGenerator.verify_key(_SECBAAS_KEY, _SECBAAS_HASH) is True


def test_secbaas_hash_rejects_a_different_key() -> None:
    assert APIKeyGenerator.verify_key("0" * 32, _SECBAAS_HASH) is False


def test_secbaas_fixture_uses_documented_pbkdf2_parameters() -> None:
    """Read side: recompute secbaas's derived key from the salt it carries."""
    salt_b64, dk_b64 = _SECBAAS_HASH.split(":")
    salt, stored_dk = base64.b64decode(salt_b64), base64.b64decode(dk_b64)

    assert len(salt) == 32
    assert len(stored_dk) == 32
    assert stored_dk == hashlib.pbkdf2_hmac(
        hash_name="sha256",
        password=_SECBAAS_KEY.encode(),
        salt=salt,
        iterations=100_000,
    )


def test_hash_key_output_uses_documented_pbkdf2_parameters() -> None:
    """Write side: the same pinning against *our* ``hash_key`` output.

    Without this, weakening the salt width or the iteration count in this copy
    passes every other test in any checkout that has no ``src/baas`` on disk —
    the internal round-trips stay self-consistent under any parameter change
    applied to ``hash_key`` and ``verify_key`` together.
    """
    key = APIKeyGenerator.generate()
    salt_b64, dk_b64 = APIKeyGenerator.hash_key(key).split(":")
    salt, stored_dk = base64.b64decode(salt_b64), base64.b64decode(dk_b64)

    assert len(salt) == 32
    assert len(stored_dk) == 32
    assert stored_dk == hashlib.pbkdf2_hmac(
        hash_name="sha256", password=key.encode(), salt=salt, iterations=100_000
    )


def test_generate_is_32_char_base62() -> None:
    key = APIKeyGenerator.generate()
    assert len(key) == 32
    assert re.fullmatch(r"[0-9A-Za-z]{32}", key)
    assert APIKeyGenerator.validate_format(key) is True


def test_generate_is_not_deterministic() -> None:
    assert len({APIKeyGenerator.generate() for _ in range(16)}) == 16


def test_hash_roundtrip() -> None:
    key = APIKeyGenerator.generate()
    assert APIKeyGenerator.verify_key(key, APIKeyGenerator.hash_key(key)) is True


def test_hashing_one_key_twice_yields_different_stored_values() -> None:
    """Fresh salt per call — and both stored values still verify the same key."""
    key = APIKeyGenerator.generate()
    first, second = APIKeyGenerator.hash_key(key), APIKeyGenerator.hash_key(key)
    assert first != second
    assert APIKeyGenerator.verify_key(key, first) is True
    assert APIKeyGenerator.verify_key(key, second) is True


def test_verify_rejects_wrong_key() -> None:
    stored = APIKeyGenerator.hash_key(APIKeyGenerator.generate())
    assert APIKeyGenerator.verify_key(APIKeyGenerator.generate(), stored) is False


@pytest.mark.parametrize(
    "stored_hash",
    ["", "not-a-hash", "no-colon-separator", "!!!:???", "onlysalt:", ":onlydk"],
)
def test_verify_rejects_unusable_stored_hash(stored_hash: str) -> None:
    """A stored value that cannot yield a matching digest returns False.

    Not "every corrupt value is rejected" — see
    :func:`test_verify_tolerates_non_base64_noise_in_stored_hash`.
    """
    assert APIKeyGenerator.verify_key(_SECBAAS_KEY, stored_hash) is False


def test_verify_tolerates_non_base64_noise_in_stored_hash() -> None:
    """Characterization of an upstream quirk, not an endorsement.

    ``base64.b64decode`` defaults to non-validating, so characters outside the
    alphabet are discarded rather than rejected: a stored hash corrupted *only*
    by inserted punctuation decodes to the original bytes and still verifies.
    Failing closed here means passing ``validate=True`` in secbaas's copy first.
    """
    assert APIKeyGenerator.verify_key(_SECBAAS_KEY, _SECBAAS_HASH + "!!!") is True


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("5X1tk2yC6rxmKhUfWzN2GJ3CYiGGE22F", True),
        ("", False),
        ("short", False),
        ("a" * 31, False),
        ("a" * 33, False),
        ("a" * 31 + "-", False),  # base62 excludes punctuation
        ("a" * 31 + "_", False),
        ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhIjoxfQ.sig", False),  # a JWT
    ],
)
def test_validate_format(candidate: str, expected: bool) -> None:
    assert APIKeyGenerator.validate_format(candidate) is expected


def test_validate_format_accepts_a_trailing_newline() -> None:
    """Characterization of an upstream quirk, not an endorsement.

    ``re.match`` with a ``$`` anchor also matches immediately before a trailing
    newline, so a 33-character value ending in ``\\n`` passes as a 32-char key
    (``\\Z`` or ``re.fullmatch`` would not). Harmless where this predicate is
    used for credential dispatch — both branches reject such a value — but wrong
    if it is ever used to validate input. Fixing it means editing secbaas's copy.
    """
    assert APIKeyGenerator.validate_format("a" * 32 + "\n") is True


def test_round_trip_against_secbaas_implementation() -> None:
    """Both directions: each implementation verifies the other's output."""
    secbaas = _load_secbaas_generator(_secbaas_source()).APIKeyGenerator

    ours = APIKeyGenerator.generate()
    assert secbaas.verify_key(ours, APIKeyGenerator.hash_key(ours)) is True

    theirs = secbaas.generate()
    assert APIKeyGenerator.verify_key(theirs, secbaas.hash_key(theirs)) is True


def test_copy_is_byte_identical_to_secbaas_source() -> None:
    """The copy must not drift; edit the scheme in both places or neither."""
    ours = _repo_root() / _OURS_RELPATH
    assert ours.read_bytes() == _secbaas_source().read_bytes()
