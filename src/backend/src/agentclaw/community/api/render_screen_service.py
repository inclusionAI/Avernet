"""Service API Protocol for bot render-screen records."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RenderScreenServiceProtocol(Protocol):
    """Service API for render-screen CRUD."""

    def list_render_screens(self, *, bot_id: str, owner_id: str) -> list[Any]: ...

    def create_render_screen(self, *args: Any, **kwargs: Any) -> Any: ...

    def update_render_screen(self, *args: Any, **kwargs: Any) -> Any: ...

    def delete_render_screen(self, *, record_id: int) -> None: ...

    def get_render_screen(self, record_id: int) -> Any | None: ...
