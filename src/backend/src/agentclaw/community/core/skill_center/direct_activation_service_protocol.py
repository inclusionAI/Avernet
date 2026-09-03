"""Service API for direct (Set-free) capability activation on one Bot."""

from __future__ import annotations

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
    """

    async def activate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]: ...

    async def deactivate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]: ...

    async def activate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]: ...

    async def deactivate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]: ...

    def list_installed_mcps(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> set[str]:
        """The Bot's active MCP server codes — the query twin of the commands
        above, answered by the capability state reader (which flushes first)."""
        ...

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
