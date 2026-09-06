"""Service API for direct (Set-free) capability activation on one Bot."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DirectActivationServiceProtocol(Protocol):
    """Activate/deactivate ONE capability (skill or MCP) for a Bot, directly.

    Legal only when no Set or platform Default policy governs the capability.
    Platform Default MCPs are controlled only by Default exclusion/un-exclusion.
    Same authorization, same UoW write, same compensation as the Set service:
    one pattern, two scopes.

    ``project=False`` (W8) records the desired state and skips both the
    readiness check and the runtime projection — for a delivery that projects
    by itself, which is teclaw's artifact. The default is the pre-W8 contract.

    **This is not the manifest apply engine's ``ActivationPort``.** That port is
    these six methods *without* ``project``, because choosing whether to project
    belongs to the delivery strategy rather than to a materialiser. It is not a
    supertype of this Protocol and this service is not bound to it: the two
    wrappers in ``bot_config_manifest/apply/activation_delegates.py`` hold a
    ``DirectActivationServiceProtocol`` and delegate to it with the ``project``
    value their family requires.
    """

    @abstractmethod
    async def activate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def deactivate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def activate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def deactivate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def list_installed_mcps(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> set[str]:
        """The Bot's active MCP server codes — the query twin of the commands
        above, answered by the capability state reader (which flushes first)."""
        ...

    @abstractmethod
    def platform_default_mcp_codes(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> frozenset[str]:
        """The codes this Bot's engine/template policy owns.

        The query twin of the *refusal* above: ``activate_mcp`` and
        ``deactivate_mcp`` raise ``SkillSetControlPlaneConflictError`` on exactly
        these codes, and a caller that wants to know before it starts writing
        has no other way to ask.

        It exists so a desired-state caller can honour this class's opening rule
        — direct control is "legal only when no Set or platform Default policy
        governs the capability" — *before* the first write rather than by
        catching the conflict from the middle of a loop. Answered from the same
        policy object the commands consult, so the two answers cannot diverge.
        """
        ...
