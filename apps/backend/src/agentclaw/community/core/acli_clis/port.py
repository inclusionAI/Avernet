"""What the acli CLI service needs from a bot's running container.

The container runs ``acli``; the platform only asks it to. Every failure
reaches the service as :class:`AcliPortError` with a short ``code`` the
service and the HTTP layer branch on:

* ``not_running`` — the bot has no reachable container.
* ``engine_unsupported`` — the engine build has no acli endpoints.
* ``invalid_input`` — the engine refused the request as malformed.
* ``timeout`` / ``engine_unreachable`` — the call itself failed.
* any code the engine's runner reported (``install_failed``,
  ``uninstall_failed``, ``acli_unavailable``, ``market_failed``, …).
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class AcliPortError(Exception):
    """The container could not do what was asked. ``message`` is caller-safe."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AcliTool:
    """One acli market tool, as acli describes it."""

    name: str
    command: str
    description: str
    version: str


@dataclass(frozen=True, slots=True)
class AcliMarketPage:
    items: tuple[AcliTool, ...]
    total: int
    has_more: bool


@runtime_checkable
class AcliPort(Protocol):
    """Install, uninstall and search acli tools in one bot's container."""

    @abstractmethod
    async def install(
        self, *, bot_id: str, owner_id: str, name: str, install_dir: str
    ) -> AcliTool:
        """Install ``name``; raises :class:`AcliPortError` on any refusal."""
        ...

    @abstractmethod
    async def uninstall(
        self, *, bot_id: str, owner_id: str, name: str, install_dir: str
    ) -> bool:
        """Uninstall ``name``. ``False`` means acli had nothing installed."""
        ...

    @abstractmethod
    async def market(
        self, *, bot_id: str, owner_id: str, keyword: str, page_no: int, page_size: int
    ) -> AcliMarketPage:
        """One page of the acli market, searched from inside the container."""
        ...


__all__ = ["AcliMarketPage", "AcliPort", "AcliPortError", "AcliTool"]
