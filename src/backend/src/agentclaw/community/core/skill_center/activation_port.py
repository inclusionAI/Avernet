"""The narrow activation contract: the six methods, without the projection choice.

``DirectActivationServiceProtocol`` is the full Service API — these six methods
plus ``project``, which lets a caller record desired state *and* decide whether
the runtime projection runs. This is the subset seen by a caller for whom that
choice does not exist.

Two classes implement it, and the split is the reason it is a type rather than
a comment. ``DirectActivationService`` implements it by *widening*: its own
Protocol extends this one and adds ``project``, so the ARCA apply path hands
the real service straight to the ``mcp`` and ``skills`` materialisers.
``RecordOnlyActivation`` implements it *directly*: it wraps that same service
and pins ``project=False`` on every write, so it cannot accept the parameter at
all — a surface the wide Protocol therefore cannot describe.

**Why it lives here rather than beside the materialisers that consume it.**
``core.skill_center`` must not import ``core.bot_config_manifest`` — the
dependency runs one way, and ``bot_config_manifest/README.md`` declares it — so
a contract the service is required to *name in its bases* has to be reachable
from this side. Both implementers declare it; the pairing is not left to DI
wiring for a reader to reconstruct.

Members are ``@abstractmethod`` on purpose. The backend has no static type
checker, so a structurally-satisfied Protocol is verified by nothing at all;
abstract members make a dropped or renamed method fail at construction instead
of resolving to an inherited ``...`` stub that silently returns ``None``.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Iterable, Protocol, runtime_checkable


@runtime_checkable
class ActivationPort(Protocol):
    """Activation as the ``mcp`` and ``skills`` materialisers ask for it."""

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
