"""Direct (Set-free) activation commands: one capability, one Bot.

The command-side twin of the Set service, sharing its UoW, its ownership
policy (R1 decided inside the write transaction) and its
mutate-project-compensate flow — one pattern, two scopes. Each wire keeps its
established error vocabulary: the skill commands speak the Local Skill
family, the MCP commands the SkillSet control-plane family.
"""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.authorization_hook import (
    BotCapabilityAuthorizationHookProtocol,
)
from agentclaw.community.core.skill_center.bot_engine_scope import (
    bot_default_engine_types,
    bot_engine_type,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    LocalSkillRuntimeSyncError,
    McpPermissionDeniedError,
    SkillSetAccessDeniedError,
    SkillSetControlPlaneNotFoundError,
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.policies.platform_default_mcp import (
    PlatformDefaultMcpPolicy,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
)
from agentclaw.community.core.skill_center.services._mutation_flow import (
    MutationProjectionFlow,
    mcp_claim_scope,
    mcp_release_scope,
    skill_claim_scope,
    skill_release_scope,
)
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.core.skill_center.direct_activation_service_protocol import DirectActivationServiceProtocol


class DirectActivationService(DirectActivationServiceProtocol):
    """Activate/deactivate ONE capability (skill or MCP) for a Bot, directly.

    Legal only when no Set governs it (Policy R1, enforced by the UoW under
    the transaction). The engine scope deliberately uses the *widening*
    read-side helpers: for a refusal guard, a Bot with no persisted engine
    must match more Sets, not none — the fail-safe direction.
    """

    @inject
    def __init__(
        self,
        repository: CapabilityDesiredStateRepositoryProtocol,
        bot_repo: BotRepository,
        skill_repo: SkillRepository,
        runtime: BotRuntimeProjectorProtocol,
        authorization: BotCapabilityAuthorizationHookProtocol,
        audit_log_repo: BotCollabLogRepositoryProtocol,
        mcp_center: MCPCenterPlugin,
        reader: BotCapabilityStateReaderProtocol,
        platform_default_mcp_policy: PlatformDefaultMcpPolicy,
    ) -> None:
        self._repository = repository
        self._bot_repo = bot_repo
        self._skill_repo = skill_repo
        self._authorization = authorization
        self._audit_log_repo = audit_log_repo
        self._mcp_center = mcp_center
        self._reader = reader
        self._platform_default_mcp_policy = platform_default_mcp_policy
        self._flow = MutationProjectionFlow(repository=repository, runtime=runtime)

    # ── Skills ──────────────────────────────────────────────────────

    async def activate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        return await self._set_skill_active(
            skill_id=skill_id, bot_id=bot_id, owner_id=owner_id,
            actor_id=actor_id, active=True, project=project,
        )

    async def deactivate_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        return await self._set_skill_active(
            skill_id=skill_id, bot_id=bot_id, owner_id=owner_id,
            actor_id=actor_id, active=False, project=project,
        )

    async def _set_skill_active(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str,
        active: bool, project: bool = True,
    ) -> dict[str, Any]:
        skill, bot = self._resolve_skill(
            skill_id=skill_id, bot_id=bot_id, owner_id=owner_id,
            actor_id=actor_id,
        )
        command = (
            self._repository.install_skill if active
            else self._repository.uninstall_skill
        )
        try:
            result = await self._flow.apply(
                bot=bot,
                bot_id=bot_id,
                engine_type=bot_engine_type(bot),
                # ``project=False`` records the desired state and skips both
                # the readiness check and the runtime projection (W8): the
                # teclaw strategy delivers the whole artifact itself, before
                # the container exists, so there is nothing to project onto.
                runtime_required=project,
                mutation=lambda: command(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    skill_id=str(skill["id"]),
                    engine_type=bot_engine_type(bot),
                    default_engine_types=bot_default_engine_types(bot),
                ),
                # Skills only — unless the Skill carries MCP dependencies,
                # which join the Bot's projected MCP set along with it. The
                # repository names them on the mutation result, read under the
                # row lock, exactly as ``add_skill`` does; declaring
                # ``mcp=False`` regardless would leave a dependency
                # whitelisted but never configured on the device.
                scope_from_result=(
                    skill_claim_scope if active else skill_release_scope
                ),
            )
        except SkillSetControlPlaneNotFoundError as exc:
            raise LocalSkillNotFoundError() from exc
        except SkillSetRuntimeReconcileError as exc:
            raise LocalSkillRuntimeSyncError() from exc
        self._audit(
            bot_id=bot_id, owner_id=owner_id, actor_id=actor_id,
            action="skill_direct_activate" if active else "skill_direct_deactivate",
        )
        return {
            **skill,
            "active": active,
            "changed": result["changed"],
            "runtime_projection": result["runtime_projection"],
        }

    def _resolve_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, actor_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Resolve the asset and authorize the actor against the Bot.

        A Local row carries its own Bot/owner, so the addressed pair must be
        exactly it; shared governed Repo rows are system-owned and take the
        addressed pair. Authorization failure masks as not-found — the Local
        wire's published behavior.
        """
        if not skill_id.isdecimal():
            raise LocalSkillNotFoundError()
        raw = self._skill_repo.get_by_id(skill_id)
        if raw is None:
            raise LocalSkillNotFoundError()
        git_path = str(raw.get("git_path") or "")
        if git_path.startswith("local://"):
            if (
                str(raw.get("user_id") or "") != owner_id
                or str(raw.get("bolt_id") or "") != bot_id
                or not raw.get("user_id")
            ):
                raise LocalSkillNotFoundError()
            skill = self._skill_repo.get_bot_local_skill(
                skill_id=skill_id, bot_id=bot_id, user_id=owner_id
            )
            if skill is None:
                raise LocalSkillNotFoundError()
        elif git_path.startswith("git://"):
            # The old scanner persisted ``bolt_id=default`` on some rows; it
            # is a storage sentinel, never ownership.
            if raw.get("user_id"):
                raise LocalSkillNotFoundError()
            skill = {**raw, "bolt_id": bot_id, "user_id": owner_id}
        else:
            raise LocalSkillNotFoundError()
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise LocalSkillNotFoundError()
        if not self._authorization.can_manage_bot(
            bot_id=bot_id, owner_id=owner_id, actor_id=actor_id
        ):
            raise LocalSkillNotFoundError()
        return skill, bot

    # ── MCPs ────────────────────────────────────────────────────────

    async def activate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, actor_id=actor_id)
        platform_default_codes = self._platform_default_codes(bot, server_code)
        self._require_mcp_permission(actor_id=actor_id, server_code=server_code)
        result = await self._flow.apply(
            bot=bot,
            bot_id=bot_id,
            engine_type=bot_engine_type(bot),
            runtime_required=project,  # W8: see ``_set_skill_active``
            mutation=lambda: self._repository.install_mcp(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                server_code=server_code,
                platform_default_codes=platform_default_codes,
                engine_type=bot_engine_type(bot),
                default_engine_types=bot_default_engine_types(bot),
            ),
            scope_from_result=mcp_claim_scope,
        )
        self._audit(
            bot_id=bot_id, owner_id=str(bot["owner_id"]), actor_id=actor_id,
            action="mcp_direct_activate",
        )
        return result

    async def deactivate_mcp(
        self, *, server_code: str, bot_id: str, owner_id: str, actor_id: str,
        project: bool = True,
    ) -> dict[str, Any]:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, actor_id=actor_id)
        platform_default_codes = self._platform_default_codes(bot, server_code)
        result = await self._flow.apply(
            bot=bot,
            bot_id=bot_id,
            engine_type=bot_engine_type(bot),
            runtime_required=project,  # W8: see ``_set_skill_active``
            mutation=lambda: self._repository.uninstall_mcp(
                bot_id=bot_id,
                owner_id=str(bot["owner_id"]),
                server_code=server_code,
                platform_default_codes=platform_default_codes,
                engine_type=bot_engine_type(bot),
                default_engine_types=bot_default_engine_types(bot),
            ),
            scope_from_result=mcp_release_scope,
        )
        self._audit(
            bot_id=bot_id, owner_id=str(bot["owner_id"]), actor_id=actor_id,
            action="mcp_direct_deactivate",
        )
        return result

    def list_installed_mcps(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> set[str]:
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, actor_id=actor_id)
        return set(
            self._reader.active_mcp_server_codes(
                bot_id=bot_id, owner_id=str(bot["owner_id"]), bot=bot
            )
        )

    def platform_default_mcp_codes(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> frozenset[str]:
        """``server_codes_for``, behind the same Bot resolution as the commands.

        Deliberately the policy object the commands already hold rather than a
        second construction: a caller asking "would activate_mcp refuse this?"
        must get the answer activate_mcp will actually give.
        """
        bot = self._bot(bot_id=bot_id, owner_id=owner_id, actor_id=actor_id)
        return self._platform_default_mcp_policy.server_codes_for(bot)

    # ── Shared ──────────────────────────────────────────────────────

    def _platform_default_codes(
        self, bot: dict, server_code: str
    ) -> frozenset[str]:
        return self._platform_default_mcp_policy.require_direct_control_allowed(
            bot=bot,
            server_code=server_code,
        )

    def _bot(self, *, bot_id: str, owner_id: str, actor_id: str) -> dict:
        """Resolve the exact addressed Bot; the MCP wire's error vocabulary."""
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            raise SkillSetControlPlaneNotFoundError()
        if not self._authorization.can_manage_bot(
            bot_id=bot_id, owner_id=owner_id, actor_id=actor_id
        ):
            raise SkillSetAccessDeniedError()
        return bot

    def _require_mcp_permission(self, *, actor_id: str, server_code: str) -> None:
        result = self._mcp_center.check_mcp_permission_detail(actor_id, server_code)
        # The catalogue endpoint deliberately reports fail-open during an
        # upstream outage.  Desired-state writes cannot use that advisory
        # answer: an empty access level is its documented outage sentinel, so
        # installing then must fail closed.
        if not bool(result.get("has_permission")) or not result.get("access_level"):
            raise McpPermissionDeniedError()

    def _audit(
        self, *, bot_id: str, owner_id: str, actor_id: str, action: str
    ) -> None:
        self._audit_log_repo.insert(
            {
                "bot_id": bot_id,
                "owner_id": owner_id,
                "operator_id": actor_id,
                "detail": f'{{"action":"{action}"}}',
            }
        )
