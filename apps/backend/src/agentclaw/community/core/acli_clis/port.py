"""What the acli CLI service needs from a bot runtime's container.

The container runs ``acli``; the platform only asks it to. Every install and
uninstall is an engine **operation**: submitted, then read back by id until it
ends. Every failure reaches the service as :class:`AcliPortError` with a short
``code`` the service and the HTTP layer branch on:

* ``not_running`` — the runtime has no reachable container.
* ``engine_unsupported`` — the engine answered 501: it refuses acli.
* ``engine_outdated`` — a 404 for an install, uninstall, cancel or listing:
  the engine does not provide these endpoints (an older build, or an engine
  that does not implement them).
* ``engine_busy`` — another operation holds the container (``operation`` is it).
* ``not_active`` — a cancel found nothing of that kind running.
* ``invalid_input`` — the engine refused the request as malformed.
* ``timeout`` / ``engine_unreachable`` — the call itself failed (a 404 while
  reading back an operation the engine accepted is ``engine_unreachable``).
* ``bad_output`` — the engine answered something unreadable.
* any code the engine's runner reported (``list_failed``, ``acli_unavailable``, …).
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.acli_clis.operations import (
    AcliAction,
    AcliRuntime,
    EngineOperation,
)


class AcliPortError(Exception):
    """The container could not do what was asked. ``message`` is caller-safe.

    ``operation`` is the engine's active operation on ``engine_busy``.
    """

    def __init__(
        self, code: str, message: str, *, operation: EngineOperation | None = None
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.operation = operation


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
    """acli operations in one bot runtime's container."""

    @abstractmethod
    async def submit(
        self, runtime: AcliRuntime, *, action: AcliAction, name: str
    ) -> EngineOperation:
        """Start an ``install`` or ``uninstall`` of ``name`` without waiting for it."""
        ...

    @abstractmethod
    async def get_operation(
        self, runtime: AcliRuntime, operation_id: str
    ) -> EngineOperation | None:
        """The engine operation ``operation_id``; ``None`` when the engine has no record."""
        ...

    @abstractmethod
    async def cancel(
        self, runtime: AcliRuntime, *, action: AcliAction, name: str
    ) -> EngineOperation:
        """Ask the engine to cancel the running ``action`` of ``name``, without waiting."""
        ...

    @abstractmethod
    async def list_tools(self, runtime: AcliRuntime) -> list[AcliTool]:
        """What acli has installed in the runtime's container."""
        ...


__all__ = ["AcliMarketPage", "AcliPort", "AcliPortError", "AcliTool"]
