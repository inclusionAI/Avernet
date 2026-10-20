"""The RSI-02 contract files, loaded once from the package copy.

``contract/`` holds byte-identical copies of ``genome-revision.schema.json``,
``genome-patch.schema.json`` and ``risk-tiers.json`` from
``apps/backend/api-contracts/genome/v1/``. The copies exist because the wheel
packages ``src/`` only; ``tests/community/core/bot_genome/test_contract_copies.py``
pins them equal to the originals, so there is one source of truth.

Validators are draft-07 (the schemas' own ``$schema``), with both files
registered under their ``$id`` so the patch schema's cross-file ``$ref``s
resolve.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource

CONTRACT_DIR = Path(__file__).resolve().parent / "contract"
REVISION_SCHEMA_FILE = "genome-revision.schema.json"
PATCH_SCHEMA_FILE = "genome-patch.schema.json"
RISK_TIERS_FILE = "risk-tiers.json"
CONTRACT_FILES = (REVISION_SCHEMA_FILE, PATCH_SCHEMA_FILE, RISK_TIERS_FILE)


def _load(name: str) -> Any:
    return json.loads((CONTRACT_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    """``(revision schema, patch schema)`` as parsed JSON."""
    return _load(REVISION_SCHEMA_FILE), _load(PATCH_SCHEMA_FILE)


@lru_cache(maxsize=1)
def _registry() -> Registry:
    revision, patch = schemas()
    return Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in (revision, patch)
    )


@lru_cache(maxsize=1)
def revision_validator() -> jsonschema.Draft7Validator:
    """Validates a whole Genome Revision record (``genome_schema``, ``revision``, ``spec``, ``policy``)."""
    return jsonschema.Draft7Validator(schemas()[0], registry=_registry())


@lru_cache(maxsize=1)
def patch_validator() -> jsonschema.Draft7Validator:
    """Validates a Genome Patch."""
    return jsonschema.Draft7Validator(schemas()[1], registry=_registry())


@lru_cache(maxsize=8)
def definition_validator(schema_file: str, definition: str) -> jsonschema.Draft7Validator:
    """A validator for one ``definitions/<name>`` of a contract schema, e.g. ``policy`` or ``memoryItems``."""
    revision, patch = schemas()
    schema = revision if schema_file == REVISION_SCHEMA_FILE else patch
    return jsonschema.Draft7Validator(
        {"$ref": schema["$id"] + "#/definitions/" + definition}, registry=_registry()
    )


@lru_cache(maxsize=1)
def risk_tiers() -> dict[str, Any]:
    """``risk-tiers.json``: the per-op rules, the raises and the rewrite threshold (README §6.3)."""
    return _load(RISK_TIERS_FILE)


def schema_errors(validator: jsonschema.Draft7Validator, record: Any) -> list[str]:
    """Every schema violation of ``record`` as ``"<json path>: <message>"``, in document order."""
    errors = sorted(validator.iter_errors(record), key=lambda error: list(error.absolute_path))
    out = []
    for error in errors:
        path = "/".join(str(part) for part in error.absolute_path) or "$"
        out.append(f"{path}: {error.message}")
    return out
