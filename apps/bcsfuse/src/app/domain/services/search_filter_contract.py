"""Bounded search-filter contract shared by V1 search and recommendation."""

from __future__ import annotations

from search_core.api.filters import SEARCH_FILTER_FIELDS

SUPPORTED_V1_FILTER_FIELDS = frozenset(SEARCH_FILTER_FIELDS)
MAX_V1_FILTER_FIELDS = 16
MAX_V1_FILTER_VALUES_PER_FIELD = 32
MAX_V1_FILTER_VALUE_LENGTH = 256


def validate_v1_search_filters(filters: object | None) -> None:
    """Reject unsupported or oversized filters before business execution.

    V1 filter values are strings or string arrays. This validator intentionally
    reports field names only, never filter values.
    """
    if filters is None:
        return
    if not isinstance(filters, dict):
        raise ValueError("filters must be an object")
    if len(filters) > MAX_V1_FILTER_FIELDS:
        raise ValueError(
            f"filters must contain at most {MAX_V1_FILTER_FIELDS} fields"
        )
    unsupported = sorted(set(filters) - SUPPORTED_V1_FILTER_FIELDS)
    if unsupported:
        raise ValueError("unsupported filter fields: " + ", ".join(unsupported))
    for field, values in filters.items():
        # V1 compatibility allows a single string. It is bounded below just
        # like a one-element array and remains a string-only type contract.
        normalized_values = [values] if isinstance(values, str) else values
        if not isinstance(normalized_values, list):
            raise ValueError(f"filter {field} must be a string or string array")
        if len(normalized_values) > MAX_V1_FILTER_VALUES_PER_FIELD:
            raise ValueError(
                f"filter {field} must contain at most "
                f"{MAX_V1_FILTER_VALUES_PER_FIELD} values"
            )
        for value in normalized_values:
            if not isinstance(value, str):
                raise ValueError(f"filter {field} values must be strings")
            if len(value) > MAX_V1_FILTER_VALUE_LENGTH:
                raise ValueError(
                    f"filter {field} values must contain at most "
                    f"{MAX_V1_FILTER_VALUE_LENGTH} characters"
                )


__all__ = [
    "MAX_V1_FILTER_FIELDS",
    "MAX_V1_FILTER_VALUE_LENGTH",
    "MAX_V1_FILTER_VALUES_PER_FIELD",
    "SUPPORTED_V1_FILTER_FIELDS",
    "validate_v1_search_filters",
]
