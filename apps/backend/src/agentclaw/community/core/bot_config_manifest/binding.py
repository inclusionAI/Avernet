"""Consistency checks for creation tasks bound to a stored Manifest revision."""

import hashlib
import json
import yaml


class ManifestBindingMismatchError(ValueError):
    """The stored document differs from the creation task binding."""


def manifest_digest(document: str) -> str:
    parsed = yaml.safe_load(document)
    return hashlib.sha256(
        json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def verify_bound_document(document: str | None, expected_digest: str) -> None:
    try:
        matches = document is not None and manifest_digest(document) == expected_digest
    except (yaml.YAMLError, ValueError, TypeError, UnicodeError):
        matches = False
    if not matches:
        raise ManifestBindingMismatchError(
            "bound Manifest has changed; creation stopped without replacing the stored document"
        )
