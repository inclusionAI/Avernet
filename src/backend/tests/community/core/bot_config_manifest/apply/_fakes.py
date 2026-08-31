"""Fakes for the apply engine's tests.

Each one **counts its calls**, because most of what these tests assert is the
*absence* of a write: convergence is "applying an unchanged document performs no
write", and all-or-nothing is "a category that could not be materialised wrote
nothing". Equal-looking output would prove neither.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.capabilities import (
    resolve_capabilities,
)


class FakeStartupScriptService:
    """Stands in for ``BotStartupScriptService``, recording every call."""

    def __init__(self, body: str = "") -> None:
        self.body = body
        self.puts: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []

    def get_body(self, *, entity_id: str, bot_id: str) -> str:
        return self.body

    def put(self, *, entity_id: str, bot_id: str, script: str, modifier: str) -> None:
        self.puts.append(
            {
                "entity_id": entity_id,
                "bot_id": bot_id,
                "script": script,
                "modifier": modifier,
            }
        )
        self.body = script

    def delete(self, *, entity_id: str, bot_id: str) -> bool:
        self.deletes.append({"entity_id": entity_id, "bot_id": bot_id})
        existed = bool(self.body)
        self.body = ""
        return existed

    @property
    def writes(self) -> int:
        return len(self.puts) + len(self.deletes)


class PlatformPolicyConflict(Exception):
    """Stands in for ``SkillSetControlPlaneConflictError``.

    Imported by shape rather than by name to keep these fakes free of the
    skill_center import graph; what matters to the engine is that the real
    service raises *something* from inside a write.
    """


class FakeActivationService:
    """Stands in for ``DirectActivationService``, recording every call.

    ``platform_defaults`` models the guard the real service runs *before* the
    permission check: ``activate_mcp``/``deactivate_mcp`` raise
    ``SkillSetControlPlaneConflictError`` on an engine/template default code.
    The fake raises too — without that, a materialiser that forgot to ask about
    platform defaults would pass every test here while half-writing the category
    in production, which is exactly the gap this models.
    """

    def __init__(
        self,
        installed: set[str] | None = None,
        platform_defaults: set[str] | None = None,
    ) -> None:
        self.installed = set(installed or ())
        self.platform_defaults = set(platform_defaults or ())
        self.activated: list[str] = []
        self.deactivated: list[str] = []

    def list_installed_mcps(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> set[str]:
        return set(self.installed)

    def platform_default_mcp_codes(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> frozenset[str]:
        return frozenset(self.platform_defaults)

    async def activate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        self._refuse_if_platform_owned(server_code)
        self.activated.append(server_code)
        self.installed.add(server_code)
        return {}

    async def deactivate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, Any]:
        self._refuse_if_platform_owned(server_code)
        self.deactivated.append(server_code)
        self.installed.discard(server_code)
        return {}

    def _refuse_if_platform_owned(self, server_code: str) -> None:
        if server_code in self.platform_defaults:
            raise PlatformPolicyConflict("RESOURCE_MANAGED_BY_PLATFORM_POLICY")

    @property
    def writes(self) -> int:
        return len(self.activated) + len(self.deactivated)


class FakeMcpAuth:
    """Permission answers, per server code.

    ``denied`` names servers the tenant may not enable; ``outage`` names ones
    whose lookup returns the fail-open shape the catalogue gives during an
    upstream outage — which a desired-state write must read as "no".
    """

    def __init__(
        self, denied: set[str] | None = None, outage: set[str] | None = None
    ) -> None:
        self.denied = set(denied or ())
        self.outage = set(outage or ())

    def check_mcp_permission_detail(
        self, user_id: str, server_code: str
    ) -> dict[str, Any]:
        if server_code in self.denied:
            return {"has_permission": False, "access_level": None}
        if server_code in self.outage:
            # The documented outage sentinel: advisory "yes" with no level.
            return {"has_permission": True, "access_level": None}
        return {"has_permission": True, "access_level": "PUBLIC"}


def make_context(
    *,
    bot_id: str = "b_1",
    owner_id: str = "u_owner",
    actor_id: str = "u_actor",
    entity_id: str = "u_owner",
    engine_type: str = "claude_code",
    bot_type: str = "personal",
) -> ApplyContext:
    """An ``ApplyContext`` with real capabilities resolved for a baas bot."""
    return ApplyContext(
        bot_id=bot_id,
        owner_id=owner_id,
        actor_id=actor_id,
        entity_id=entity_id,
        env="dev",
        tenant="teamclaw",
        engine_type=engine_type,
        bot_type=bot_type,
        bot={
            "bot_id": bot_id,
            "owner_id": owner_id,
            "entity_id": entity_id,
            "active_engine": engine_type,
            "bot_type": bot_type,
        },
        capabilities=resolve_capabilities(
            active_engine=engine_type,
            bot_type=bot_type,
            is_teclaw=lambda engine: engine == "teclaw",
        ),
    )
