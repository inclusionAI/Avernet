"""Domain errors for public Bot render-screen configuration operations."""

from __future__ import annotations


class RenderScreenNotFoundError(Exception):
    """The addressed Bot or render-screen record is unavailable to the caller."""


class RenderScreenConflictError(Exception):
    """The requested configuration conflicts with an existing record."""
