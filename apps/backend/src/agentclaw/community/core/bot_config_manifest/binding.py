"""Consistency checks for creation tasks bound to a stored Manifest revision."""

import hashlib
import json
import re
import yaml

from agentclaw.community.core.apply_engine.compiler import StateRef

DIGEST_ENCODING = "sha256-json-v1"


class ManifestBindingMismatchError(ValueError):
    """The stored document differs from the creation task binding."""


def manifest_digest(document: str) -> str:
    return parsed_manifest_digest(yaml.safe_load(document))


def parsed_manifest_digest(parsed: object) -> str:
    """Hash the exact parsed input that the compiler will consume."""
    return hashlib.sha256(
        json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def bound_manifest_state(expected_digest: str) -> StateRef:
    """A producer-owned assertion on the bot's existing document, not a copy."""
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise ManifestBindingMismatchError("invalid bound Manifest digest")
    return StateRef("manifest", f"{DIGEST_ENCODING}:{expected_digest}")


def expected_manifest_digest(ref: StateRef) -> str | None:
    if ref.reference is None:
        return None
    if not isinstance(ref.reference, str) or not re.fullmatch(
        rf"{DIGEST_ENCODING}:[0-9a-f]{{64}}", ref.reference
    ):
        raise ManifestBindingMismatchError("invalid bound Manifest reference")
    return ref.reference.split(":", 1)[1]


def verify_parsed_document(parsed: object, expected_digest: str) -> None:
    try:
        matches = parsed_manifest_digest(parsed) == expected_digest
    except (ValueError, TypeError, UnicodeError):
        matches = False
    if not matches:
        raise ManifestBindingMismatchError(
            "bound Manifest has changed; creation stopped without replacing the stored document"
        )


def verify_bound_document(document: str | None, expected_digest: str) -> None:
    try:
        matches = document is not None and manifest_digest(document) == expected_digest
    except (yaml.YAMLError, ValueError, TypeError, UnicodeError):
        matches = False
    if not matches:
        raise ManifestBindingMismatchError(
            "bound Manifest has changed; creation stopped without replacing the stored document"
        )
