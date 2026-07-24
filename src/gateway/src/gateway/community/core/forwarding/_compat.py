"""Backward-compatibility check between two published OpenAPI descriptions.

A published version may keep evolving as long as changes are backward-compatible;
this classifies a candidate description against the currently-published one and
returns the breaking changes (empty list ⇒ safe to publish). It is deliberately
focused — the well-known breaking classes — not a full OpenAPI differ.

Pure logic (Rule 7): no web framework, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_METHODS = frozenset(
    {"get", "put", "post", "delete", "patch", "options", "head", "trace"}
)


@dataclass(frozen=True)
class Breaking:
    """One backward-incompatible change."""

    kind: str
    where: str
    detail: str = ""


def check_compatible(old: dict[str, Any], new: dict[str, Any]) -> list[Breaking]:
    """Return the breaking changes going from *old* to *new* (empty ⇒ compatible)."""
    breaks: list[Breaking] = []
    _check_operations(old, new, breaks)
    _check_schemas(old, new, breaks)
    return breaks


# ── operations ───────────────────────────────────────────────────────────────


def _operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    ops: dict[tuple[str, str], dict[str, Any]] = {}
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method.lower() in _METHODS and isinstance(operation, dict):
                ops[(method.lower(), path)] = operation
    return ops


def _check_operations(
    old: dict[str, Any], new: dict[str, Any], breaks: list[Breaking]
) -> None:
    old_ops = _operations(old)
    new_ops = _operations(new)
    for key, old_op in old_ops.items():
        method, path = key
        new_op = new_ops.get(key)
        if new_op is None:
            breaks.append(Breaking("operation-removed", f"{method.upper()} {path}"))
            continue
        _check_parameters(method, path, old_op, new_op, breaks)


def _check_parameters(
    method: str,
    path: str,
    old_op: dict[str, Any],
    new_op: dict[str, Any],
    breaks: list[Breaking],
) -> None:
    old_params = _params_by_name(old_op)
    for param in new_op.get("parameters") or []:
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        loc = param.get("in")
        if not name:
            continue
        was = old_params.get((name, loc))
        # A parameter that is now required but wasn't before (or is brand new and
        # required) breaks existing callers.
        if param.get("required") and not (was and was.get("required")):
            breaks.append(
                Breaking(
                    "parameter-now-required",
                    f"{method.upper()} {path}",
                    f"{loc} param {name!r}",
                )
            )


def _params_by_name(op: dict[str, Any]) -> dict[tuple[Any, Any], dict[str, Any]]:
    out: dict[tuple[Any, Any], dict[str, Any]] = {}
    for param in op.get("parameters") or []:
        if isinstance(param, dict) and param.get("name"):
            out[(param.get("name"), param.get("in"))] = param
    return out


# ── component schemas ────────────────────────────────────────────────────────


def _schemas(spec: dict[str, Any]) -> dict[str, Any]:
    schemas = (spec.get("components") or {}).get("schemas") or {}
    return schemas if isinstance(schemas, dict) else {}


def _check_schemas(
    old: dict[str, Any], new: dict[str, Any], breaks: list[Breaking]
) -> None:
    old_schemas = _schemas(old)
    new_schemas = _schemas(new)
    for name, old_schema in old_schemas.items():
        new_schema = new_schemas.get(name)
        if new_schema is None:
            breaks.append(Breaking("schema-removed", name))
            continue
        if isinstance(old_schema, dict) and isinstance(new_schema, dict):
            _check_schema(name, old_schema, new_schema, breaks)


def _check_schema(
    name: str,
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
    breaks: list[Breaking],
) -> None:
    old_props = old_schema.get("properties") or {}
    new_props = new_schema.get("properties") or {}
    for prop, old_prop in old_props.items():
        new_prop = new_props.get(prop)
        if new_prop is None:
            breaks.append(Breaking("property-removed", f"{name}.{prop}"))
            continue
        if isinstance(old_prop, dict) and isinstance(new_prop, dict):
            _check_property(f"{name}.{prop}", old_prop, new_prop, breaks)

    old_required = set(old_schema.get("required") or [])
    new_required = set(new_schema.get("required") or [])
    for prop in sorted(new_required - old_required):
        breaks.append(Breaking("property-now-required", f"{name}.{prop}"))


def _check_property(
    where: str,
    old_prop: dict[str, Any],
    new_prop: dict[str, Any],
    breaks: list[Breaking],
) -> None:
    old_type = old_prop.get("type")
    new_type = new_prop.get("type")
    if old_type is not None and new_type is not None and old_type != new_type:
        breaks.append(Breaking("type-changed", where, f"{old_type} → {new_type}"))

    if "default" in old_prop and old_prop.get("default") != new_prop.get("default"):
        breaks.append(Breaking("default-changed", where))

    old_enum = old_prop.get("enum")
    new_enum = new_prop.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        removed = [v for v in old_enum if v not in new_enum]
        if removed:
            breaks.append(Breaking("enum-value-removed", where, f"removed {removed}"))
