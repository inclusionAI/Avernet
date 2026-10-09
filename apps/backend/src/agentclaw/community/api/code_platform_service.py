"""Service API Protocol for a code/git platform integration.

The corp deployment binds the AntCode implementation; community binds a no-op.
The Protocol name stays vendor-neutral so the seam carries no corp branding.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class CodePlatformServiceProtocol(Protocol):
    """Service API for code-platform project listing + token resolution."""

    def get_private_token(self, cookie: Optional[str] = None) -> Optional[str]: ...

    def search_user_projects(self, *args: Any, **kwargs: Any) -> Any: ...
