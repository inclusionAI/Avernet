"""Shared utilities for SQLite repository implementations."""

import json
from typing import Any


def parse_json_field(value: Any, default: Any = None) -> Any:
    """Parse a JSON-stored SQLite TEXT column into a Python object.

    In MySQL, JSON columns auto-deserialize. In SQLite, JSON is stored as TEXT
    and must be deserialized manually. This helper centralizes that logic.

    Args:
        value: The raw column value (str, dict/list, or None).
        default: Fallback value if parsing fails or value is empty.
                 Defaults to empty dict.

    Returns:
        Parsed Python object, or default on failure.
    """
    if default is None:
        default = {}
    try:
        if isinstance(value, str):
            return json.loads(value) if value else default
        return value if value is not None else default
    except (json.JSONDecodeError, TypeError):
        return default
