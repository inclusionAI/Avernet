"""Semantic failures for the Todo workbench domain."""

from agentclaw.community.core.errors import Conflict, Forbidden, NotFound


class TodoNotFoundError(NotFound):
    """The requested Todo does not exist in the current tenant/environment."""


class TodoStateConflictError(Conflict):
    """The requested mutation is not legal in the Todo's current state."""


class TodoBotScopeError(Forbidden):
    """The selected Bot is not eligible for the Todo's Space."""


class TodoBotAmbiguousError(Conflict):
    """More than one eligible live Bot shares the selected Bot id."""
