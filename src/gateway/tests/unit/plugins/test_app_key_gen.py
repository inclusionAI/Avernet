"""Unit tests for ``APIKeyGenerator`` — the app credential scheme.

The module under test copies the secbaas API-gateway key generator, because
secbaas's existing ``baas_api_key`` records are migrated into
``avernet_application`` and must keep verifying with their original plaintext
keys. Its code is byte-identical to upstream's; only its comments and docstrings
differ, being in English per the convention the rest of the gateway follows.
:func:`test_copy_is_semantically_identical_to_secbaas_source` is what allows that
gap and nothing wider. The tests here pin the compatibility from both ends:

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
  key. Nothing consumes this predicate yet; when Task 4 builds the credential
  dispatch on it, the property to test there is that such a value authenticates
  on neither path — not the quirk itself, which is upstream's to fix.
* ``verify_key`` decodes with a non-validating ``base64.b64decode``, so a stored
  hash corrupted only by characters outside the base64 alphabet still verifies.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import inspect
import re
import secrets
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

# The monorepo root is the ancestor holding both module trees. Derived from the
# two paths above so that moving a module leaves one place to update *in this
# file* — a second hardcoded "src/baas" here would make the guidance in
# _secbaas_source() insufficient, since the probe would stop matching before the
# relpath is ever read. The path is also spelled out in the docstring of
# gateway/community/core/app/__init__.py, which nothing enforces.
_MODULE_DIRS = tuple(
    Path(*Path(rel).parts[:2]) for rel in (_SECBAAS_RELPATH, _OURS_RELPATH)
)


def _monorepo_root() -> Path | None:
    """The ancestor holding both module trees, or ``None`` if there is none.

    Keyed on the module directories rather than on ``AGENTS.md``: this repo
    already places that file at module level (``src/bcs``, ``src/frontend``), so
    a future ``src/gateway/AGENTS.md`` would silently resolve the wrong root.

    ``None`` is ambiguous between "the gateway was split out on its own" and
    "a module directory was renamed", and is treated as the former — the tests
    skip. That is a real gap: see the note in ``plan.md`` on the CI gate scoring
    skips as passes.
    """
    for parent in Path(__file__).resolve().parents:
        if all((parent / module).is_dir() for module in _MODULE_DIRS):
            return parent
    return None


def _require_monorepo_root() -> Path:
    root = _monorepo_root()
    if root is None:
        pytest.skip(
            f"no ancestor holds all of {[str(m) for m in _MODULE_DIRS]}; "
            "parity against secbaas is not checkable from this checkout"
        )
    return root


def _secbaas_source(root: Path | None = None) -> Path:
    source = (root or _require_monorepo_root()) / _SECBAAS_RELPATH
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


_DEFINITION = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _code_fingerprint(source: Path) -> str:
    """A source file reduced to what it *does*, ignoring comments and prose.

    Comments never reach the syntax tree, and the docstring of every definition
    is dropped here, so the result changes only when an executable construct
    does. ``ast.dump`` defaults to omitting line and column attributes, which is
    what makes reformatting invisible too.

    Not a checksum of the text: ``0x64`` and ``100`` would fingerprint alike, as
    would a renamed local. Neither weakens the guarantee this backs, since the
    scheme's ingredients are compared as the literals they are written as.
    """
    tree = ast.parse(source.read_bytes())
    for node in ast.walk(tree):
        if not isinstance(node, _DEFINITION):
            continue
        body = node.body
        first = body[0] if body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            # ``or [ast.Pass()]`` because a body that was *only* a docstring is
            # not a legal empty body, and dumping it would raise rather than
            # report the mismatch this function exists to report.
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree)


def _split_stored_hash(stored: str) -> tuple[bytes, bytes]:
    """Decode a stored hash, checking each half re-encodes to what was stored.

    The re-encode catches an alphabet switch for any payload that contains a
    distinguishing character, which is most but not all of them: standard and
    urlsafe base64 emit byte-identical output when a payload happens to contain
    none, so this cannot pin the alphabet on its own for a random salt. The
    exact pin lives in :func:`test_hash_key_encodes_with_standard_base64`, which
    injects a salt chosen to distinguish them.
    """
    salt_b64, dk_b64 = stored.split(":")
    salt, dk = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
    assert base64.b64encode(salt).decode() == salt_b64, "salt is not standard base64"
    assert base64.b64encode(dk).decode() == dk_b64, "digest is not standard base64"
    return salt, dk


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

    Digest, iteration count, and salt width are deterministic in a single
    sample; the base64 alphabet is not, and is pinned separately by
    :func:`test_hash_key_encodes_with_standard_base64`.
    """
    key = APIKeyGenerator.generate()
    salt, stored_dk = _split_stored_hash(APIKeyGenerator.hash_key(key))

    assert len(salt) == 32
    assert len(stored_dk) == 32
    assert stored_dk == hashlib.pbkdf2_hmac(
        hash_name="sha256", password=key.encode(), salt=salt, iterations=100_000
    )


def test_hash_key_encodes_with_standard_base64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the alphabet exactly, using a salt that distinguishes the two.

    A random salt cannot do this: standard and urlsafe base64 differ only on
    bytes that encode to ``+``/``/`` (resp. ``-``/``_``), and a payload
    containing none of them encodes identically under both. Injecting a salt
    whose encoding contains both characters turns a probabilistic check into an
    exact one — and makes a failure name the expected string rather than show a
    bytes diff.
    """
    # 0x3E/0x3F are the two byte values whose sixtets encode to the characters
    # the alphabets disagree on, so this salt exercises both of them.
    salt = bytes((0x3E, 0x3F)) * 16
    expected_salt_b64 = base64.b64encode(salt).decode()
    assert "+" in expected_salt_b64 and "/" in expected_salt_b64
    monkeypatch.setattr(secrets, "token_bytes", lambda _n: salt)

    salt_b64, _ = APIKeyGenerator.hash_key(APIKeyGenerator.generate()).split(":")

    assert salt_b64 == expected_salt_b64, (
        "stored salt is not standard base64 — a urlsafe_b64encode switch would "
        f"render this as {base64.urlsafe_b64encode(salt).decode()!r}"
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
        "!!!:???",  # decodes to empty; compare_digest then mismatches
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


def test_copy_is_semantically_identical_to_secbaas_source() -> None:
    """The copy must not drift; edit the scheme in both places or neither.

    Two things must hold. The class must be *defined in the gateway package* —
    checked via ``__module__``, which is immune to install layout, where a path
    comparison would miss a re-export from an installed secbaas elsewhere on
    ``sys.path``. And what the two files *do* must be identical.

    Compared as syntax trees with docstrings stripped rather than byte for byte,
    so that the two files may carry different prose: this copy's comments are in
    English, per the convention every other gateway source file follows, while
    upstream's are in Chinese. Every ingredient of the stored hash — the base62
    alphabet, the digest, the iteration count, the salt width, the encoding, the
    ``salt:dk`` join — is a literal or a call in that tree, so a one-sided change
    to any of them still fails here.
    """
    assert APIKeyGenerator.__module__ == "gateway.community.core.app._key_gen", (
        f"APIKeyGenerator is defined in {APIKeyGenerator.__module__}, not the "
        "gateway's own copy — the plan rejects importing secbaas's class, and "
        "a parity check against a re-export would pass vacuously."
    )

    root = _require_monorepo_root()
    tree_copy = root / _OURS_RELPATH
    if not tree_copy.is_file():
        pytest.fail(
            f"gateway key generator not found at {tree_copy}; if it moved, "
            "update _OURS_RELPATH rather than letting this check lapse."
        )
    assert _code_fingerprint(tree_copy) == _code_fingerprint(_secbaas_source(root)), (
        "the gateway's key generator no longer matches secbaas's. Migrated "
        "records verify only while both derive the same digest, so port the "
        "change to the other file rather than relaxing this test."
    )


def test_copy_carries_no_cjk_prose() -> None:
    """The convention the parity test deliberately stops enforcing.

    Dropping byte-identity is what lets this copy be translated, and re-copying
    upstream wholesale is the natural way to keep the scheme in sync — which
    would silently restore the Chinese comments. This is the guard that makes
    the translation survive that.

    Scoped to this one file: Chinese is the established convention in
    ``migrations/mysql``, whose comments ship to the database as column
    ``COMMENT``s, and is untouched there. Read from the imported module rather
    than from the tree, so it holds wherever the package is installed from and
    needs no ``src/baas`` to run.

    The bound is ``U+2FFF`` — above every CJK ideograph, CJK punctuation and
    fullwidth form, below the punctuation English prose here actually uses (em
    dashes at ``U+2014``, curly quotes at ``U+2018``).
    """
    source = Path(inspect.getsourcefile(APIKeyGenerator) or "").read_text(
        encoding="utf-8"
    )
    offenders = sorted({ch for ch in source if ord(ch) > 0x2FFF})
    assert not offenders, (
        f"CJK characters in the gateway's key generator: {''.join(offenders)!r}. "
        "Comments and docstrings in this copy are translated; re-copying upstream "
        "verbatim reintroduces them. Port the code change by hand, keeping the "
        "English prose."
    )
