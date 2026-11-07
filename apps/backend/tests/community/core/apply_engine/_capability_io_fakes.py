"""Fakes for the apply engine's tests.

Each one **counts its calls**, because most of what these tests assert is the
*absence* of a write: convergence is "applying an unchanged document performs no
write", and all-or-nothing is "a category that could not be materialised wrote
nothing". Equal-looking output would prove neither.
"""

from __future__ import annotations


from agentclaw.community.core.mcp.config_comparison import comparable_config
from agentclaw.community.core.ports.mcp_config import McpConfigWriteIntent

from typing import Any


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

    Both pairs the real service owns — the MCP pair and the skill pair —
    because both materialisers share one service instance and a fake split by
    pair would pass the registry while failing the first apply that touched
    the other half.

    ``platform_defaults`` models the guard the real service runs *before* the
    permission check: ``activate_mcp``/``deactivate_mcp`` raise
    ``SkillSetControlPlaneConflictError`` on an engine/template default code.
    The fake raises too — without that, a materialiser that forgot to ask about
    platform defaults would pass every test here while half-writing the category
    in production, which is exactly the gap this models. ``governed_skills``
    models the same refusal for skills: ids a Set or the default set supplies.

    What the bot's Sets supply (``set_supplied_capabilities``) is ``set_managed`` plus ``platform_defaults``
    for MCP, and ``set_skill_names`` for skills; the apply's claims refuse those, as the real repository does.
    """

    def __init__(
        self,
        installed: set[str] | None = None,
        platform_defaults: set[str] | None = None,
        governed_skills: set[int] | None = None,
    ) -> None:
        self.installed = set(installed or ())
        self.platform_defaults = set(platform_defaults or ())
        self.set_managed: set[str] = set()
        self.set_skill_names: set[str] = set()
        self.mcp_overrides: dict[str, dict] = {}
        self.configured: list[tuple[str, dict | None]] = []
        self.governed_skills = set(governed_skills or ())
        self.activated: list[str] = []
        self.deactivated: list[str] = []
        self.installed_skills: set[int] = set()
        self.skill_dependencies: dict[int, frozenset[str]] = {}
        self.skill_activations: list[int] = []
        self.skill_deactivations: list[int] = []
        # The ``project`` flag of every write (W8): the real service projects
        # by default; the record-only wrapper passes ``False``.
        self.projections: list[bool] = []

    def list_installed_mcps(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> set[str]:
        return set(self.installed)

    def get_mcp_overrides(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> dict[str, dict]:
        return dict(self.mcp_overrides)

    def set_supplied_capabilities(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> tuple[frozenset[str], frozenset[str]]:
        return frozenset(self.set_skill_names), frozenset(
            self.set_managed | self.platform_defaults
        )

    async def activate_mcp(
        self,
        *,
        server_code: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
        self._refuse_if_platform_owned(server_code)
        self.activated.append(server_code)
        self.installed.add(server_code)
        return {}

    async def set_mcp_override(
        self,
        *,
        server_code: str,
        config: dict | None,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
        self._refuse_if_platform_owned(server_code)
        self.installed.add(server_code)
        self.configured.append((server_code, config))
        if config:
            self.mcp_overrides[server_code] = dict(config)
        else:
            self.mcp_overrides.pop(server_code, None)
        return {}

    async def deactivate_mcp(
        self,
        *,
        server_code: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
        self._refuse_if_platform_owned(server_code)
        self.deactivated.append(server_code)
        self.installed.discard(server_code)
        self.mcp_overrides.pop(server_code, None)
        return {}

    async def activate_skill(
        self,
        *,
        skill_id: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
        self._refuse_if_governed(skill_id)
        changed = int(skill_id) not in self.installed_skills
        self.installed_skills.add(int(skill_id))
        self.skill_activations.append(int(skill_id))
        return {"id": skill_id, "changed": changed}

    async def deactivate_skill(
        self,
        *,
        skill_id: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
        self._refuse_if_governed(skill_id)
        changed = int(skill_id) in self.installed_skills
        self.installed_skills.discard(int(skill_id))
        self.skill_deactivations.append(int(skill_id))
        return {"id": skill_id, "changed": changed}

    async def claim_apply_skill(
        self,
        *,
        skill_id: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        apply_id: str | None,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
        self._refuse_if_governed(skill_id)
        changed = int(skill_id) not in self.installed_skills
        self.installed_skills.add(int(skill_id))
        self.skill_activations.append(int(skill_id))
        return {"id": skill_id, "changed": changed, "outcome": "created" if changed else "unchanged"}

    async def remove_apply_skill(
        self,
        *,
        skill_id: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        apply_id: str | None,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
        self._refuse_if_governed(skill_id)
        changed = int(skill_id) in self.installed_skills
        self.installed_skills.discard(int(skill_id))
        self.skill_deactivations.append(int(skill_id))
        return {"id": skill_id, "changed": changed}

    async def claim_apply_mcp(
        self,
        *,
        server_code: str,
        config: McpConfigWriteIntent[dict[str, Any]],
        bot_id: str,
        owner_id: str,
        actor_id: str,
        apply_id: str | None,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
        self._refuse_if_set_supplied(server_code)
        effective_before = server_code in self.installed or any(
            server_code in self.skill_dependencies.get(sid, ())
            for sid in self.installed_skills
        )
        changed = server_code not in self.installed
        if server_code not in self.installed:
            self.activated.append(server_code)
        self.installed.add(server_code)
        if comparable_config(self.mcp_overrides.get(server_code)) == comparable_config(
            config
        ):
            return {"changed": changed, "outcome": "unchanged" if effective_before else "created"}
        self.configured.append((server_code, config))
        if config:
            self.mcp_overrides[server_code] = dict(config)
        else:
            self.mcp_overrides.pop(server_code, None)
        return {"changed": True, "outcome": "updated" if effective_before else "created"}

    async def remove_apply_mcp(
        self,
        *,
        server_code: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        apply_id: str | None,
        project: bool = True,
    ) -> dict[str, Any]:
        self.projections.append(project)
        self._refuse_if_set_supplied(server_code)
        changed = server_code in self.installed or server_code in self.mcp_overrides
        self.deactivated.append(server_code)
        self.installed.discard(server_code)
        self.mcp_overrides.pop(server_code, None)
        return {"changed": changed}

    def _refuse_if_set_supplied(self, server_code: str) -> None:
        if server_code in self.set_managed | self.platform_defaults:
            raise PlatformPolicyConflict("APPLY_SET_SUPPLIED")

    def _refuse_if_platform_owned(self, server_code: str) -> None:
        if server_code in self.platform_defaults:
            raise PlatformPolicyConflict("RESOURCE_MANAGED_BY_PLATFORM_POLICY")

    def _refuse_if_governed(self, skill_id: str) -> None:
        if int(skill_id) in self.governed_skills:
            raise PlatformPolicyConflict("RESOURCE_MANAGED_BY_SKILL_SET")

    @property
    def writes(self) -> int:
        return (
            len(self.activated)
            + len(self.deactivated)
            + len(self.configured)
            + len(self.skill_activations)
            + len(self.skill_deactivations)
        )


class FakeGitClient:
    """The git transport for suites whose documents fetch nothing — every
    call is a wiring bug, so it counts and refuses.

    ``constructed`` is the class-level construction count the lifecycle
    suite reads: one session per apply means one client per apply, and the
    provider (not the test) is what builds it, so the only observable of
    "a session was built" is how many times the provider ran.
    """

    constructed: int = 0

    def __init__(self) -> None:
        self.calls = 0
        type(self).constructed += 1

    def fetch(self, spec, *, headers=None):
        self.calls += 1
        raise AssertionError(
            "this suite's document declares no git sources; "
            f"git fetch({spec.url!r}) must not run"
        )


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


class FakeMcpConfigService:
    def validate_bot_override(self, *, server_code, **_kwargs) -> dict[str, Any]:
        return {
            "valid": True,
            "error": None,
            "mcp_data": {
                "serverCode": server_code,
                "runMode": "REMOTE",
                "endpoints": [{
                    "env": "PROD",
                    "networkType": "INTERNET",
                    "transportProtocol": "STREAMABLE_HTTP",
                    "url": "https://mcp.example/" + server_code,
                }],
            },
        }

    def get_bot_override(self, **_kwargs):
        return None

    def build_mcp_sync_payload(self, **_kwargs):
        return None, {}, "PROD", "STREAMABLE_HTTP"
