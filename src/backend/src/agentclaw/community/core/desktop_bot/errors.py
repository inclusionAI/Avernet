"""Desktop Bot domain errors.

Kept outside the concrete service implementation so delivery adapters and
response mappers can depend on stable domain error types without importing the
service graph.
"""
from __future__ import annotations


class DesktopBotServiceError(Exception):
    """Desktop bot service error."""


class DesktopBotOrphanError(DesktopBotServiceError):
    """BaaS confirmed bot not found (orphan)."""
