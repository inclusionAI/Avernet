"""The two ``ActivationPort`` implementations, one per delivery family (W8).

``DirectActivationService`` writes the desired state and then projects it to
the running container, and its ``project`` parameter selects whether that
second half runs. Which value is right is a property of the *delivery family*,
not of the capability being written:

* **ARCA** takes everything as writes into a live container, so a write that
  did not project has not arrived. ``project=True``.
* **platform-managed teclaw** composes an artifact from the same rows and the
  strategy closes the apply with one whole-artifact redeliver, so a
  per-mutation projection would be one extra container round-trip per skill
  and per MCP server — each redundant, and each made by an apply that needs no
  container at all. ``project=False``.

So neither value belongs to the ``mcp`` and ``skills`` materialisers, and
``ActivationPort`` — what those materialisers hold — does not expose the
parameter. These two classes are where the choice is made instead: peers, both
declaring the port, both wrapping the same service, differing in one constant.

**The service is never bound to the port raw.** It would satisfy the port by
shape, and ARCA would then project because ``project`` *defaults* to ``True``
— a family's delivery semantics resting on a parameter default, while the
other family states its choice in source. Wrapping both makes the pair
symmetric and the choice greppable: these are the only two places in the
codebase that pass ``project`` at all.
"""
from __future__ import annotations

from typing import Any, Iterable

from agentclaw.community.core.ports.activation_port import (
    ActivationPort,
)
from agentclaw.community.core.skill_center.direct_activation_service_protocol import (
    DirectActivationServiceProtocol,
)


class _DelegatingActivation(ActivationPort):
    """Forwards the port's six methods to the service, pinning ``project``.

    Subclasses set ``_PROJECT`` and add nothing else; writing the delegation
    twice would let the two families drift apart in a method neither subclass
    is about.
    """

    #: The ``project`` value every write is forwarded with.
    _PROJECT: bool

    def __init__(self, inner: DirectActivationServiceProtocol) -> None:
        self._inner = inner

    # ── reads, unchanged: `project` is a write-side choice ───────────────

    def list_installed_mcps(self, *, bot_id: str, owner_id: str, actor_id: str) -> set[str]:
        return self._inner.list_installed_mcps(
            bot_id=bot_id, owner_id=owner_id, actor_id=actor_id
        )

    def platform_default_mcp_codes(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> Iterable[str]:
        return self._inner.platform_default_mcp_codes(
            bot_id=bot_id, owner_id=owner_id, actor_id=actor_id
        )

    # ── writes, each pinned to this family's projection choice ───────────

    async def activate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        return await self._inner.activate_mcp(
            server_code=server_code, bot_id=bot_id, owner_id=owner_id,
            actor_id=actor_id, project=self._PROJECT,
        )

    async def deactivate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        return await self._inner.deactivate_mcp(
            server_code=server_code, bot_id=bot_id, owner_id=owner_id,
            actor_id=actor_id, project=self._PROJECT,
        )

    async def activate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        return await self._inner.activate_skill(
            skill_id=skill_id, bot_id=bot_id, owner_id=owner_id,
            actor_id=actor_id, project=self._PROJECT,
        )

    async def deactivate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        return await self._inner.deactivate_skill(
            skill_id=skill_id, bot_id=bot_id, owner_id=owner_id,
            actor_id=actor_id, project=self._PROJECT,
        )


class ProjectingActivation(_DelegatingActivation):
    """ARCA: write, then project to the live container. The pre-W8 contract."""

    _PROJECT = True


class RecordOnlyActivation(_DelegatingActivation):
    """Platform-managed teclaw: record the desired state, redeliver closes it."""

    _PROJECT = False


__all__ = ["ProjectingActivation", "RecordOnlyActivation"]
