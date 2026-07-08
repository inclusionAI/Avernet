"""Pydantic Response Model Schema Snapshot Utilities.

Exports JSON Schema from Pydantic response models and compares them
against saved snapshot files. This catches:
- Fields being removed from a response model
- Fields being added to a response model (in strict mode)
- Field types changing in a response model

Usage:
    # Generate/update snapshots:
    uv run pytest tests/contracts/gateway/test_schema_conformance.py --snapshot-update

    # Validate against snapshots (normal test run):
    uv run pytest tests/contracts/gateway/test_schema_conformance.py -v
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

SNAPSHOT_DIR = Path(__file__).parent / "schema_snapshots"
RESPONSE_DATA_SNAPSHOT_DIR = SNAPSHOT_DIR / "response_data"
API_RESPONSE_SNAPSHOT_DIR = SNAPSHOT_DIR / "api_response"


def model_to_json_schema(model_cls: type[BaseModel]) -> dict[str, Any]:
    """Export a Pydantic model's JSON Schema.

    Uses model_json_schema() which respects Pydantic v2 conventions.
    """
    return model_cls.model_json_schema()


def save_snapshot(model_cls: type[BaseModel], name: str | None = None) -> Path:
    """Save a model's JSON Schema as a snapshot file.

    Args:
        model_cls: Pydantic model class.
        name: Optional snapshot name (defaults to model class name).

    Returns:
        Path to the saved snapshot file.
    """
    snapshot_name = name or model_cls.__name__
    snapshot_path = SNAPSHOT_DIR / f"{snapshot_name}.json"
    schema = model_to_json_schema(model_cls)
    snapshot_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    return snapshot_path


def load_snapshot(name: str) -> dict[str, Any] | None:
    """Load a snapshot file by name. Returns None if not found."""
    snapshot_path = SNAPSHOT_DIR / f"{name}.json"
    if not snapshot_path.exists():
        return None
    return json.loads(snapshot_path.read_text())


def diff_schemas(current: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    """Compare two JSON Schemas and return a list of differences.

    Only checks properties at the top level and nested $defs — not
    full recursive diff, since deeply nested $ref structures are
    generated mechanically by Pydantic.

    Returns:
        List of human-readable diff lines. Empty if schemas match.
    """
    diffs: list[str] = []

    # Compare top-level properties
    curr_props = current.get("properties", {})
    snap_props = snapshot.get("properties", {})

    curr_fields = set(curr_props.keys())
    snap_fields = set(snap_props.keys())

    added = curr_fields - snap_fields
    removed = snap_fields - curr_fields

    if added:
        diffs.append(f"  fields added: {sorted(added)}")
    if removed:
        diffs.append(f"  fields removed: {sorted(removed)}")

    # Check type changes for common fields
    for field in sorted(curr_fields & snap_fields):
        curr_type = curr_props[field].get("type") or curr_props[field].get("$ref", "")
        snap_type = snap_props[field].get("type") or snap_props[field].get("$ref", "")
        if curr_type != snap_type:
            diffs.append(f"  field '{field}' type changed: {snap_type!r} -> {curr_type!r}")

    # Compare $defs (nested model definitions)
    curr_defs = current.get("$defs", {})
    snap_defs = snapshot.get("$defs", {})

    curr_def_names = set(curr_defs.keys())
    snap_def_names = set(snap_defs.keys())

    added_defs = curr_def_names - snap_def_names
    removed_defs = snap_def_names - curr_def_names

    if added_defs:
        diffs.append(f"  $defs added: {sorted(added_defs)}")
    if removed_defs:
        diffs.append(f"  $defs removed: {sorted(removed_defs)}")

    # Check property changes within shared $defs
    for def_name in sorted(curr_def_names & snap_def_names):
        curr_def_props = curr_defs[def_name].get("properties", {})
        snap_def_props = snap_defs[def_name].get("properties", {})
        curr_def_fields = set(curr_def_props.keys())
        snap_def_fields = set(snap_def_props.keys())

        def_added = curr_def_fields - snap_def_fields
        def_removed = snap_def_fields - curr_def_fields

        if def_added or def_removed:
            diffs.append(f"  $defs.{def_name}:")
            if def_added:
                diffs.append(f"    fields added: {sorted(def_added)}")
            if def_removed:
                diffs.append(f"    fields removed: {sorted(def_removed)}")

        # Type changes within nested def properties
        for field in sorted(curr_def_fields & snap_def_fields):
            curr_type = curr_def_props[field].get("type") or curr_def_props[field].get("$ref", "")
            snap_type = snap_def_props[field].get("type") or snap_def_props[field].get("$ref", "")
            if curr_type != snap_type:
                diffs.append(
                    f"  $defs.{def_name}.{field} type changed: {snap_type!r} -> {curr_type!r}"
                )

    # Compare required fields at top level
    curr_required = set(current.get("required", []))
    snap_required = set(snapshot.get("required", []))
    if curr_required != snap_required:
        new_req = curr_required - snap_required
        no_longer_req = snap_required - curr_required
        if new_req:
            diffs.append(f"  newly required: {sorted(new_req)}")
        if no_longer_req:
            diffs.append(f"  no longer required: {sorted(no_longer_req)}")

    return diffs


def validate_model_against_snapshot(
    model_cls: type[BaseModel],
    name: str | None = None,
    *,
    update: bool = False,
) -> list[str]:
    """Validate a model's current schema against its snapshot.

    Args:
        model_cls: Pydantic model class to validate.
        name: Optional snapshot name (defaults to model class name).
        update: If True, save the current schema as the new snapshot.

    Returns:
        List of diff lines. Empty if schemas match or update=True.
    """
    snapshot_name = name or model_cls.__name__

    if update:
        save_snapshot(model_cls, snapshot_name)
        return []

    snapshot = load_snapshot(snapshot_name)
    if snapshot is None:
        # First run — save baseline snapshot
        save_snapshot(model_cls, snapshot_name)
        return []

    current = model_to_json_schema(model_cls)
    return diff_schemas(current, snapshot)


# ── BCS/Engine Contract Schema Validation ──────────────────────────────────


def validate_mock_against_schema(
    mock_data: dict[str, Any] | list[Any],
    schema: dict[str, Any],
    label: str = "",
) -> None:
    """Validate mock response data against a JSON Schema contract.

    This breaks the tautology where mock data and assertions share
    the same source — the schema becomes the authority, and mock data
    must conform to it.

    Args:
        mock_data: The mock response data to validate.
        schema: JSON Schema dict to validate against.
        label: Human-readable label for error messages.

    Raises:
        AssertionError: If validation fails.
    """
    import jsonschema as _jsonschema

    try:
        _jsonschema.validate(instance=mock_data, schema=schema)
    except _jsonschema.ValidationError as e:
        path = ".".join(str(p) for p in e.absolute_path) if e.absolute_path else "(root)"
        raise AssertionError(
            f"{label} mock data does not match JSON Schema contract at {path}: {e.message}"
        ) from e


def load_contract_schema(name: str) -> dict[str, Any]:
    """Load a BCS/Engine contract schema from schema_snapshots/bcs/.

    Args:
        name: Schema filename without .json extension (e.g., "group_response").

    Returns:
        Parsed JSON Schema dict.

    Raises:
        FileNotFoundError: If schema file does not exist.
    """
    schema_path = SNAPSHOT_DIR / "bcs" / f"{name}.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Contract schema not found: {schema_path}")
    return json.loads(schema_path.read_text())


# ── Actual Response Data Contract Snapshots ────────────────────────────────


def _schema_for_scalar(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    return {}


def infer_json_schema(value: Any) -> dict[str, Any]:
    """Infer a strict JSON Schema from an actual JSON-compatible value.

    Object schemas include all observed keys as required and set
    ``additionalProperties: false`` recursively. This is intentionally strict:
    the goal is to catch drift in broad ``ApiResponse`` / ``dict`` payloads.
    """
    if isinstance(value, dict):
        properties = {key: infer_json_schema(item) for key, item in sorted(value.items())}
        return {
            "type": "object",
            "properties": properties,
            "required": sorted(value.keys()),
            "additionalProperties": False,
        }

    if isinstance(value, list):
        schema: dict[str, Any] = {"type": "array"}
        if value:
            schema["items"] = merge_json_schemas([infer_json_schema(item) for item in value])
        return schema

    return _schema_for_scalar(value)


def merge_json_schemas(schemas: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge schemas inferred from array items.

    Homogeneous object arrays are merged into a single object schema whose
    allowed keys are the union of observed keys and whose required keys are
    the intersection. Mixed scalar/object arrays use ``anyOf``.
    """
    if not schemas:
        return {}

    type_keys = {json.dumps(schema.get("type"), sort_keys=True) for schema in schemas}
    if len(type_keys) != 1:
        return {"anyOf": _dedupe_schemas(schemas)}

    first_type = schemas[0].get("type")
    if first_type == "object":
        all_keys: set[str] = set()
        required_keys: set[str] | None = None
        for schema in schemas:
            props = schema.get("properties", {})
            all_keys.update(props.keys())
            required = set(schema.get("required", []))
            required_keys = required if required_keys is None else required_keys & required

        properties: dict[str, Any] = {}
        for key in sorted(all_keys):
            key_schemas = [
                schema["properties"][key]
                for schema in schemas
                if key in schema.get("properties", {})
            ]
            properties[key] = merge_json_schemas(key_schemas)

        return {
            "type": "object",
            "properties": properties,
            "required": sorted(required_keys or set()),
            "additionalProperties": False,
        }

    if first_type == "array":
        item_schemas = [schema["items"] for schema in schemas if "items" in schema]
        merged: dict[str, Any] = {"type": "array"}
        if item_schemas:
            merged["items"] = merge_json_schemas(item_schemas)
        return merged

    return schemas[0]


def _dedupe_schemas(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for schema in schemas:
        key = json.dumps(schema, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            deduped.append(schema)
    return deduped


def response_data_snapshot_path(name: str) -> Path:
    return RESPONSE_DATA_SNAPSHOT_DIR / f"{name}.json"


def save_response_data_snapshot(name: str, data: Any) -> Path:
    RESPONSE_DATA_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = response_data_snapshot_path(name)
    schema = infer_json_schema(data)
    path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    return path


def load_response_data_snapshot(name: str) -> dict[str, Any] | None:
    path = response_data_snapshot_path(name)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def validate_data_against_snapshot(name: str, data: Any, *, update: bool = False) -> None:
    """Validate actual response ``data`` against its strict schema snapshot."""
    import jsonschema as _jsonschema

    if update:
        save_response_data_snapshot(name, data)
        return

    schema = load_response_data_snapshot(name)
    if schema is None:
        raise AssertionError(
            f"Missing response data contract snapshot: {response_data_snapshot_path(name)}. "
            "Run with --snapshot-update to create it."
        )

    validator = _jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        lines = []
        for error in errors[:10]:
            path = ".".join(str(p) for p in error.absolute_path) if error.absolute_path else "(root)"
            lines.append(f"  {path}: {error.message}")
        raise AssertionError(
            f"{name} response data contract drift detected:\n" + "\n".join(lines)
        )


# ── Whole API Response Contract Snapshots ─────────────────────────────────


def api_response_snapshot_path(name: str) -> Path:
    return API_RESPONSE_SNAPSHOT_DIR / f"{name}.json"


def save_api_response_snapshot(name: str, response_json: Any) -> Path:
    API_RESPONSE_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = api_response_snapshot_path(name)
    schema = infer_json_schema(response_json)
    path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    return path


def load_api_response_snapshot(name: str) -> dict[str, Any] | None:
    path = api_response_snapshot_path(name)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def validate_api_response_against_snapshot(
    name: str,
    response_json: Any,
    *,
    update: bool = False,
) -> None:
    """Validate a complete API JSON response against its strict snapshot.

    Unlike ``validate_data_against_snapshot``, this includes top-level
    fields such as ``success``, ``message``, and ``error_code`` so response
    envelope drift is visible to contract tests.
    """
    import jsonschema as _jsonschema

    if update:
        save_api_response_snapshot(name, response_json)
        return

    schema = load_api_response_snapshot(name)
    if schema is None:
        raise AssertionError(
            f"Missing API response contract snapshot: {api_response_snapshot_path(name)}. "
            "Run with --snapshot-update to create it."
        )

    validator = _jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(response_json), key=lambda e: list(e.absolute_path))
    if errors:
        lines = []
        for error in errors[:10]:
            path = ".".join(str(p) for p in error.absolute_path) if error.absolute_path else "(root)"
            lines.append(f"  {path}: {error.message}")
        raise AssertionError(
            f"{name} API response contract drift detected:\n" + "\n".join(lines)
        )
