"""运行上下文里永远授予的部件名（06 §5.1）。"""

from __future__ import annotations

__all__ = ["ALWAYS_GRANTED_PARTS"]

ALWAYS_GRANTED_PARTS: tuple[str, ...] = (
    "parent",
    "workspace",
    "operations",
    "budget",
    "log",
    "artifacts",
    "cancelled",
)
