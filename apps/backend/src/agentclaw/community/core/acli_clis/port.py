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
    """One acli market tool, as acli describes it.

    Examples, from rows of ``acli list --keyword data --json`` (acli 2.0.7)::

        AcliTool(name="dataphin", command="dataphin",
                 description="dataphinCLI工具", version="1.4.14")
        AcliTool(name="deepinsight-cli", command="di",
                 description="1", version="1.0.9")

    The market also returns ``id`` and ``author``; they are not kept.
    """

    #: The market name, e.g. ``deepinsight-cli``. It is also the tool's code in
    #: Passport, and what ``DELETE …/clis/{cli_code}`` takes.
    name: str
    #: The executable acli puts on ``PATH``, e.g. ``di``. acli accepts it as an
    #: alias on install; the API refuses it in favour of ``name``.
    command: str
    #: Free text the tool's author wrote, e.g. ``dataphinCLI工具``; may be empty,
    #: and may be long Markdown (release notes).
    description: str
    #: The installed or latest version, e.g. ``1.0.9``; may be empty.
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
        self, *, bot_id: str, owner_id: str, name: str
    ) -> AcliTool:
        """Install ``name``; raises :class:`AcliPortError` on any refusal."""
        ...

    @abstractmethod
    async def uninstall(
        self, *, bot_id: str, owner_id: str, name: str
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
