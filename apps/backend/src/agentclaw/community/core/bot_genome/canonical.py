"""Canonical JSON and digests for the Bot Genome contract.

The contract (``apps/backend/api-contracts/genome/v1/README.md`` §3) hashes
records with the JSON Canonicalization Scheme, RFC 8785, restricted to a subset
with no floats. This module is the registry's one implementation of that rule;
``canonical-json-vectors.json`` is its oracle (``tests/community/core/bot_genome/
test_canonical.py`` and the contract test run every vector against it).

Why a subset is enough, and safe: RFC 8785 serialises numbers the way
ECMAScript does for IEEE doubles, which is the part implementations most often
get wrong. Hashed genome content has integers only, so this module refuses a
float outright instead of formatting one.

Usage::

    canonicalize({"b": 1, "a": [True, None]})   # -> b'{"a":[true,null],"b":1}'
    revision_id(spec, policy)                    # -> "sha256:7c1e…"
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (
    canonical_tree_bytes,
)

#: The integer range RFC 8785 can represent exactly (IEEE double safe integers).
MAX_SAFE_INTEGER = 2**53 - 1


class NotCanonicalizable(ValueError):
    """The value is outside the hashed-content subset (a float, a lone surrogate, …)."""


def loads_strict(text: str) -> Any:
    """Parse JSON text, refusing duplicate member names.

    ``json.loads`` keeps the last of two duplicate members silently, so the
    value it returns is not the record that was sent; RFC 8785 requires
    unique names, and so does the genome contract (README §3.1).
    """

    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        names = [name for name, _ in pairs]
        if len(names) != len(set(names)):
            raise NotCanonicalizable("duplicate object member name")
        return dict(pairs)

    return json.loads(text, object_pairs_hook=_pairs)


def canonicalize(value: Any) -> bytes:
    """The RFC 8785 canonical form of ``value`` as UTF-8 bytes."""
    return _serialize(value).encode("utf-8")


def sha256_digest(data: bytes) -> str:
    """The content-store address of ``data``: ``"sha256:" + lowercase hex``."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def as_json(value: Any) -> Any:
    """A contract model's JSON object (``to_json()``), or the value itself when it already is JSON."""
    return value.to_json() if hasattr(value, "to_json") else value


def revision_id(spec: Any, policy: Any) -> str:
    """Identity of a revision: the digest of ``JCS({"policy": …, "spec": …})``; takes the models or their JSON."""
    return sha256_digest(canonicalize({"spec": as_json(spec), "policy": as_json(policy)}))


def tree_digest(files: list[tuple[str, bytes]]) -> str:
    """Digest of a directory: its ``acm-tree-v1`` encoding (README §3.4)."""
    return sha256_digest(canonical_tree_bytes(files))


_SEGMENT_SAFE = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def encode_segment(name: str) -> str:
    """One gene-path segment in its canonical spelling (README §2)."""
    if name in (".", ".."):
        return "%2E" * len(name)
    return "".join(chr(b) if b in _SEGMENT_SAFE else f"%{b:02X}" for b in name.encode("utf-8"))


GENE_CATEGORIES = frozenset({"persona", "skills", "memory", "resources", "tools", "engine_config", "script"})


def is_canonical_gene_path(path: str) -> bool:
    """True for a gene path in the README §2 grammar whose segments are all canonically spelled."""
    category, *segments = path.split("/")
    if category not in GENE_CATEGORIES:
        return False
    for segment in segments:
        if not segment:
            return False
        try:
            decoded = _percent_decode(segment)
        except (ValueError, UnicodeDecodeError):
            return False
        if encode_segment(decoded) != segment:
            return False
    return True


def _percent_decode(segment: str) -> str:
    out = bytearray()
    i = 0
    while i < len(segment):
        if segment[i] == "%":
            out.append(int(segment[i + 1 : i + 3], 16))
            i += 3
        else:
            out += segment[i].encode("utf-8")
            i += 1
    return out.decode("utf-8")


def _serialize(value: Any) -> str:
    # ``bool`` first: it is a subclass of ``int`` and must not print as 1/0.
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise NotCanonicalizable(f"integer outside the safe range: {value}")
        return str(value)
    if isinstance(value, float):
        raise NotCanonicalizable("floats are not allowed in hashed content")
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_serialize(item) for item in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise NotCanonicalizable(f"object member name is not a string: {key!r}")
        # RFC 8785 §3.2.3: sort by UTF-16 code units. Big-endian UTF-16 bytes
        # compare in exactly that order, including surrogate pairs.
        keys = sorted(value, key=lambda name: name.encode("utf-16-be", "surrogatepass"))
        return "{" + ",".join(_string(key) + ":" + _serialize(value[key]) for key in keys) + "}"
    raise NotCanonicalizable(f"unsupported type: {type(value).__name__}")


def _string(text: str) -> str:
    """RFC 8785 §3.2.2.2 string escaping.

    The rule is ECMAScript's ``JSON.stringify``: escape ``"`` and ``\\``, write
    U+0008, U+0009, U+000A, U+000C, U+000D as ``\\b \\t \\n \\f \\r``, every other
    control character below U+0020 as ``\\u00xx`` in lowercase hex, and every
    other character as itself. ``json.dumps(..., ensure_ascii=False)`` follows
    exactly that rule, so it is reused rather than re-implemented.
    """
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:  # a lone surrogate cannot be UTF-8 encoded
        raise NotCanonicalizable("string contains a lone surrogate") from exc
    return json.dumps(text, ensure_ascii=False)
