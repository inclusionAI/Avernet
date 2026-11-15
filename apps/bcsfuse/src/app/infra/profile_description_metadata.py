"""Store Profile descriptions in the existing metadata JSON column."""

from __future__ import annotations

_DESCRIPTION_KEY = "__bcsfuse_profile_description_v1__"


def with_profile_description(metadata: dict, description: str | None) -> dict:
    """Return a storage-only copy without changing caller-visible metadata."""
    if _DESCRIPTION_KEY in metadata:
        raise ValueError(f"metadata key {_DESCRIPTION_KEY} is reserved")
    return {**metadata, _DESCRIPTION_KEY: description}


def split_profile_description(
    metadata: dict, legacy_description: str | None = None,
) -> tuple[dict, str | None]:
    """Hide the storage key and read older rows with a description column."""
    visible = dict(metadata)
    if _DESCRIPTION_KEY not in visible:
        return visible, legacy_description
    description = visible.pop(_DESCRIPTION_KEY)
    if description is not None and not isinstance(description, str):
        raise ValueError("stored Profile description must be a string or null")
    return visible, description
