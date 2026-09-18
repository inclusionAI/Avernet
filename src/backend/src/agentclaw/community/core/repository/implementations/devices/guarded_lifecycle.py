"""Shared transaction primitives for guarded Device lifecycle writes."""

from __future__ import annotations

import json
from typing import Any


def _normalize_isolation_level(value: str) -> str:
    return " ".join(
        value.replace("_", " ").replace("-", " ").upper().split()
    )


def begin_guarded_transaction(db: Any, *, purpose: str) -> None:
    """Start the explicit transaction required by guarded lifecycle writes."""
    if db.in_transaction():
        raise RuntimeError(
            f"{purpose} requires a fresh ORM Session before selecting "
            "transaction isolation"
        )
    dialect_name = db.get_bind().dialect.name
    isolation_level = "SERIALIZABLE" if dialect_name == "sqlite" else "READ COMMITTED"
    connection = db.connection(
        execution_options={"isolation_level": isolation_level}
    )
    actual_isolation = _normalize_isolation_level(connection.get_isolation_level())
    expected_isolation = _normalize_isolation_level(isolation_level)
    if actual_isolation != expected_isolation:
        db.rollback()
        raise RuntimeError(
            f"{purpose} transaction isolation mismatch: expected "
            f"{expected_isolation}, got {actual_isolation}"
        )
    if dialect_name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def load_device_props(raw: Any) -> dict[str, Any]:
    """Decode a Binding JSON value without trusting historical row shape."""
    try:
        props = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        return {}
    return props if isinstance(props, dict) else {}
