"""Domain errors raised while preparing a Bot creation request."""

from __future__ import annotations


class BotCreateError(Exception):
    """Base error for Bot creation policy failures."""


class BotTemplateInvalidError(BotCreateError):
    """The supplied Bot template attributes are malformed or unsupported."""


class BotCombinationUnsupportedError(BotCreateError):
    """The requested Bot/template combination cannot be created."""


class ApplicationCodingUnavailableError(BotCreateError):
    """Application Coding requires a Workspace Hosting capability."""
