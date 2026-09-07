"""A bot's identity files, named by what the caller needs of them.

An outbound port (see this package's README): the ``identity`` materialiser
lists, reads and writes a bot's identity files, and this names exactly those
three operations.

**It is a narrowing, and a large one.** ``IdentityService`` has fifteen public
methods — entity files as well as bot files, path resolution, validation,
``sync_agents_md``. The materialiser needs three. Handed the service a
materialiser could reach any of the other twelve; handed the port, it cannot.

**Two implementations, split on where the write lands** — the same axis the
delivery families split on everywhere else:

* ``DeviceIdentity`` (ARCA) forwards to ``IdentityService``, which writes the
  file into the bot's live container.
* ``PlatformIdentity`` (platform-managed teclaw) writes one object per identity
  file into the managed-files store and never touches a container; the composed
  artifact is the delivery.

**Signatures are positional, unlike this package's other ports.** They mirror
``IdentityService``'s own contract — ``(entity_type, entity_id, bot_id, …)``
then the operation's arguments, then owner or operator — because the device
implementation forwards to that service verbatim. A drift there surfaces as a
``TypeError`` at wiring time (the DI provider's structural check) rather than
mid-apply.

Members are ``@abstractmethod`` on purpose. The backend runs no static type
checker, so a structurally-satisfied Protocol is verified by nothing at all;
abstract members make a dropped or renamed method fail at construction instead
of resolving to an inherited ``...`` stub that silently returns ``None``.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IdentityFilePort(Protocol):
    """The three identity-file operations the ``identity`` materialiser needs."""

    @abstractmethod
    async def list_bot_files(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
        stage: str = "draft",
    ) -> list[tuple[str, bool]]: ...

    @abstractmethod
    async def read_identity_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
        stage: str = "draft",
    ) -> str: ...

    @abstractmethod
    async def update_bot_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        content: str,
        operator_id: str,
        engine_type: str | None = None,
        *,
        stage: str = "draft",
    ) -> Any: ...


__all__ = ["IdentityFilePort"]
