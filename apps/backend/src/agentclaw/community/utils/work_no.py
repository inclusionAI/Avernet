"""Helpers for normalizing employee numbers at staff-directory boundaries."""

from __future__ import annotations


def normalize_work_no_for_lookup(work_no: str) -> str:
    """Return the six-digit form expected by the staff directory when needed."""
    normalized = work_no.strip()
    if normalized.isdigit() and len(normalized) < 6:
        return normalized.zfill(6)
    return normalized
