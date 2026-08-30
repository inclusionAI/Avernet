"""Service API Protocol for the harness patch template library."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PatchLibraryProtocol(Protocol):
    """Service API for managing patch templates."""

    def list_applicable(self, *args: Any, **kwargs: Any) -> Any: ...

    def list_templates(self, *args: Any, **kwargs: Any) -> Any: ...

    def get_template_by_id(self, *args: Any, **kwargs: Any) -> Any: ...

    def create_template(self, *args: Any, **kwargs: Any) -> Any: ...

    def update_template(self, *args: Any, **kwargs: Any) -> Any: ...

    def delete_template(self, *args: Any, **kwargs: Any) -> Any: ...
