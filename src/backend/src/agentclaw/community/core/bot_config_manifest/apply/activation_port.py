"""The activation write path's narrow naming, for the materialisers' ports.

The ``mcp`` and ``skills`` materialisers record desired state through six
methods of the activation service; this names exactly those, the way
``identity_port.py`` and ``resource_port.py`` name theirs, so the ports a
delivery strategy hands ``build_materialisers`` are typed by what is called
rather than ``Any``. The bound object is the real ``DirectActivationService``
on ARCA and ``RecordOnlyActivation`` over it on the platform-managed teclaw
path — structural typing, no adapter.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Iterable, Protocol, runtime_checkable


@runtime_checkable
class ActivationPort(Protocol):
    """What the ``mcp`` and ``skills`` materialisers ask of activation."""

    @abstractmethod
    def list_installed_mcps(self, *, bot_id: str, owner_id: str, actor_id: str) -> set[str]: ...

    @abstractmethod
    def platform_default_mcp_codes(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> Iterable[str]: ...

    @abstractmethod
    async def activate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str
    ) -> Any: ...

    @abstractmethod
    async def deactivate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str
    ) -> Any: ...

    @abstractmethod
    async def activate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ) -> Any: ...

    @abstractmethod
    async def deactivate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ) -> Any: ...


__all__ = ["ActivationPort"]
