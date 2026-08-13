"""Unit tests for ``APIKeyGenerator`` — the app credential scheme.

This module is a verbatim copy of the secbaas API-gateway key generator, because
secbaas's existing ``baas_api_key`` records are migrated into
``avernet_application`` and must keep verifying with their original plaintext
keys. The tests here pin that compatibility from both ends:

* :func:`test_secbaas_produced_hash_verifies` proves we can *read* what secbaas
  wrote — it asserts against a ``(key, hash)`` pair produced by running the real
  secbaas implementation.
* :func:`test_hash_key_output_uses_documented_pbkdf2_parameters` proves we
  *write* what secbaas would. The read-side test cannot catch a weakened salt or
  iteration count in this copy, and it is the only guard in a checkout that has
  no ``src/baas`` on disk, where the parity tests below skip.

Neither parameter is recoverable from the stored string, which carries only the
salt: the digest, the iteration count, and the encoding are implicit constants
shared by both copies, so drift in any of them silently invalidates every
migrated record.

Two upstream quirks are deliberately *not* pinned here, because asserting them
would turn a future upstream fix into a failure in this suite. Both are recorded
in ``plan.md`` under "Notes on upstream follow-ups":

* ``validate_format`` uses ``re.match`` with ``$``, which also matches before a
  trailing newline, so a 33-character value ending in ``\\n`` passes as a 32-char
  key. The credential dispatch built on this predicate is tested directly for
  the property that matters — such a value never authenticates — rather than
  for the quirk itself.
* ``verify_key`` decodes with a non-validating ``base64.b64decode``, so a stored
  hash corrupted only by characters outside the base64 alphabet still verifies.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import inspect
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


def _monorepo_root() -> Path | None:
    """The ancestor holding both module trees, or ``None`` outside the monorepo.

    Keyed on the two directories rather than on ``AGENTS.md``: this repo already
    places that file at module level (``src/bcs``, ``src/frontend``), so a future
    ``src/gateway/AGENTS.md`` would silently resolve the wrong root. ``None``
    means the gateway was split out on its own, where there is no secbaas to
    compare against — distinct from "the monorepo is here but the file moved",
    which must fail loudly rather than quietly drop the parity guarantee.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "src/baas").is_dir() and (parent / "src/gateway").is_dir():
            return parent
    return None


def _secbaas_source() -> Path:
    root = _monorepo_root()
    if root is None:
        pytest.skip(
            "gateway checked out without src/baas; parity is not checkable here"
        )
    source = root / _SECBAAS_RELPATH
    if not source.is_file():
        pytest.fail(
            f"secbaas key generator not found at {source}. This copy's migration "
            "compatibility is pinned against that file; if it moved, update "
            "_SECBAAS_RELPATH rather than letting these checks lapse."
        )
    return source


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


def _split_stored_hash(stored: str) -> tuple[bytes, bytes]:
    """Decode a stored hash strictly, so the encoding itself is pinned.

    ``validate=True`` matters here: the default silently discards characters
    outside the base64 alphabet, which would let a switch to ``urlsafe_b64encode``
    pass these tests most of the time instead of failing deterministically.
    """
    salt_b64, dk_b64 = stored.split(":")
    return base64.b64decode(salt_b64, validate=True), base64.b64decode(
        dk_b64, validate=True
    )


def test_secbaas_produced_hash_verifies() -> None:
    """The migration guarantee: a secbaas-hashed key verifies under our copy."""
    assert APIKeyGenerator.verify_key(_SECBAAS_KEY, _SECBAAS_HASH) is True


def test_secbaas_hash_rejects_a_different_key() -> None:
    assert APIKeyGenerator.verify_key("0" * 32, _SECBAAS_HASH) is False


def test_pinned_fixture_is_internally_consistent() -> None:
    """Authenticate the fixture constants themselves.

    Runs no gateway code — it exists so that a mistyped or regenerated
    ``_SECBAAS_HASH`` is caught as a bad fixture rather than surfacing later as a
    confusing failure in the tests that do exercise the module.
    """
    salt, stored_dk = _split_stored_hash(_SECBAAS_HASH)

    assert len(salt) == 32
    assert len(stored_dk) == 32
    assert stored_dk == hashlib.pbkdf2_hmac(
        hash_name="sha256",
        password=_SECBAAS_KEY.encode(),
        salt=salt,
        iterations=100_000,
    )


def test_hash_key_output_uses_documented_pbkdf2_parameters() -> None:
    """Pin the write side against our own ``hash_key`` output.

    Without this, weakening the salt width or the iteration count in this copy
    passes every other test in a checkout with no ``src/baas`` on disk: the
    internal round-trips stay self-consistent under any change applied to
    ``hash_key`` and ``verify_key`` together, and the parity tests skip.
    """
    key = APIKeyGenerator.generate()
    salt, stored_dk = _split_stored_hash(APIKeyGenerator.hash_key(key))

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
    [
        "",  # empty column
        "no-colon-separator",  # unpack fails: one field
        "a:b:c",  # unpack fails: three fields, e.g. a migration concatenation
        "!!!:???",  # decodes to empty, fails on length
        "onlysalt:",
        ":onlydk",
    ],
)
def test_verify_rejects_unusable_stored_hash(stored_hash: str) -> None:
    """A stored value that cannot yield a matching digest returns False, not raises."""
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


def test_round_trip_against_secbaas_implementation() -> None:
    """Both directions: each implementation verifies the other's output."""
    secbaas = _load_secbaas_generator(_secbaas_source()).APIKeyGenerator

    ours = APIKeyGenerator.generate()
    assert secbaas.verify_key(ours, APIKeyGenerator.hash_key(ours)) is True

    theirs = secbaas.generate()
    assert APIKeyGenerator.verify_key(theirs, secbaas.hash_key(theirs)) is True


def test_copy_is_byte_identical_to_secbaas_source() -> None:
    """The copy must not drift; edit the scheme in both places or neither.

    Anchored to the module actually imported by this suite, not to a path in the
    source tree: under a non-editable install those are different files, and
    comparing the tree would pass while the loaded code was stale.
    """
    loaded = inspect.getsourcefile(APIKeyGenerator)
    assert loaded is not None
    assert Path(loaded).read_bytes() == _secbaas_source().read_bytes()
