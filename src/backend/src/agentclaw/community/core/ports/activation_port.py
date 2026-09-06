"""The activation write path, named by what the caller needs of it.

An outbound port (see this package's README): the ``mcp`` and ``skills``
materialisers record desired state through six methods of the activation
service, and this names exactly those — so the objects a delivery strategy
hands ``build_materialisers`` are typed by what is called rather than ``Any``.

**What it deliberately omits is ``project``.** ``DirectActivationServiceProtocol``
carries that parameter — record the desired state, and choose whether the
runtime projection runs with it. Choosing is the delivery strategy's job, not
the materialisers': ARCA projects as it writes, and the platform-managed teclaw
path closes the apply with one whole-artifact redeliver instead, so a
per-mutation projection there would be one redundant container round-trip per
skill and per MCP server. The materialisers must not be able to make that
choice, so the port they hold cannot offer it.

**Both implementers are wrappers, and they differ in exactly that value.**
``ProjectingActivation`` delegates with ``project=True``, ``RecordOnlyActivation``
with ``project=False``; both hold the same ``DirectActivationServiceProtocol``.
The service is never bound here raw — if it were, one family's behaviour would
rest on the parameter's *default* while the other stated its choice, and the
pair would only look symmetric.

Members are ``@abstractmethod`` on purpose. The backend runs no static type
checker, so a structurally-satisfied Protocol is verified by nothing at all;
abstract members make a dropped or renamed method fail at construction instead
of resolving to an inherited ``...`` stub that silently returns ``None``.
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
