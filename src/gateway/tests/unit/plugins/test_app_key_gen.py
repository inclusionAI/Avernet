"""Unit tests for ``APIKeyGenerator`` — the app credential scheme.

This module is a verbatim copy of the secbaas API-gateway key generator, because
secbaas's existing ``baas_api_key`` records are migrated into
``avernet_application`` and must keep verifying with their original plaintext
keys. The tests here exist to pin that compatibility:

* :func:`test_secbaas_produced_hash_verifies` asserts against a ``(key, hash)``
  pair produced by *running the real secbaas implementation*. It fails the
  moment anyone changes the digest, the iteration count, the salt handling, or
  the encoding — none of which are recoverable from the stored string, since it
  carries only the salt.
* :func:`test_round_trip_against_secbaas_implementation` loads that
  implementation from disk and checks both directions.
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

_SECBAAS_SOURCE = (
    Path(__file__).parents[5]
    / "src/baas/src/secbaas/community/core/service/api_gateway/_key_gen.py"
)


def _load_secbaas_generator() -> ModuleType:
    """Load the secbaas generator straight from disk.

    By file path rather than by package import: ``secbaas.community.api`` uses
    syntax this interpreter rejects, and the generator itself is stdlib-only, so
    the file loads cleanly on its own.
    """
    spec = importlib.util.spec_from_file_location("_secbaas_key_gen", _SECBAAS_SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_requires_secbaas = pytest.mark.skipif(
    not _SECBAAS_SOURCE.exists(),
    reason="secbaas source not present (module tested in isolation)",
)


def test_secbaas_produced_hash_verifies() -> None:
    """The migration guarantee: a secbaas-hashed key verifies under our copy."""
    assert APIKeyGenerator.verify_key(_SECBAAS_KEY, _SECBAAS_HASH) is True


def test_secbaas_hash_rejects_a_different_key() -> None:
    assert APIKeyGenerator.verify_key("0" * 32, _SECBAAS_HASH) is False


def test_stored_hash_uses_documented_pbkdf2_parameters() -> None:
    """Pin digest, iteration count, and encoding — none are in the stored string.

    Recomputes the derived key from the salt the hash carries. Any drift in
    ``sha256`` / ``100_000`` / UTF-8 password encoding / base64 breaks this,
    which is exactly the drift that would silently invalidate migrated records.
    """
    salt_b64, dk_b64 = _SECBAAS_HASH.split(":")
    salt, stored_dk = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
    assert len(salt) == 32 and len(stored_dk) == 32

    recomputed = hashlib.pbkdf2_hmac(
        hash_name="sha256",
        password=_SECBAAS_KEY.encode(),
        salt=salt,
        iterations=100_000,
    )
    assert recomputed == stored_dk


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
def test_verify_rejects_malformed_stored_hash(stored_hash: str) -> None:
    """A corrupt stored value is a rejection, never an exception."""
    assert APIKeyGenerator.verify_key(_SECBAAS_KEY, stored_hash) is False


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


@_requires_secbaas
def test_round_trip_against_secbaas_implementation() -> None:
    """Both directions: each implementation verifies the other's output."""
    secbaas = _load_secbaas_generator().APIKeyGenerator

    ours = APIKeyGenerator.generate()
    assert secbaas.verify_key(ours, APIKeyGenerator.hash_key(ours)) is True

    theirs = secbaas.generate()
    assert APIKeyGenerator.verify_key(theirs, secbaas.hash_key(theirs)) is True


@_requires_secbaas
def test_copy_is_byte_identical_to_secbaas_source() -> None:
    """The copy must not drift; edit the scheme in both places or neither."""
    ours = Path(__file__).parents[3] / "src/gateway/community/core/app/_key_gen.py"
    assert ours.read_bytes() == _SECBAAS_SOURCE.read_bytes()
