"""Runtime projection for engines whose halves have separate endpoints."""

from __future__ import annotations

from collections.abc import Sequence

from agentclaw.community.core.devices.services.device_context import (
    DeviceConnectionUnavailableError,
    DeviceOfflineError,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    EngineRuntimeProjection,
    ProjectionScope,
    ResolvedCapabilityPlan,
    ResolvedSkillPlan,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
    RuntimeServiceFactoryBoundary,
)
from agentclaw.community.core.skill_center.services.runtime_projections.skill_runtime_delivery import (
    SkillRuntimeDelivery,
)
from agentclaw.community.core.skills_pool.models import (
    PoolSkillMapping,
    RegisteredSkillAsset,
)
from agentclaw.community.log import get_logger


logger = get_logger()


class PerDomainRuntimeProjection(EngineRuntimeProjection):
    """Write each half of the projection to its own runtime endpoint.

    The contract every filesystem engine obeys: Skills reach the device as a
    symlink/mapping publish, MCPs as configuration delivery plus an allow-list
    declaration. The two are independent writes to independent endpoints, so
    re-sending the half a mutation did not touch costs a device round trip (or
    a Pool publish plus verify) to restate what is already there — which is
    what makes ``ProjectionScope``'s halves worth honouring here.
    """

    def __init__(
        self,
        *,
        skill_delivery: SkillRuntimeDelivery,
    ) -> None:
        self._skill_delivery = skill_delivery

    def validate_plan(
        self,
        *,
        skill_assets: Sequence[RegisteredSkillAsset],
        retired_mappings: Sequence[PoolSkillMapping] = (),
    ) -> None:
        """Accept every plan: these engines have the full Center contract.

        Not an oversight and not a stub. Center-corpus Skills reach a
        filesystem engine through the Skills Pool v3 mapping contract, which
        this projection publishes and verifies below, so there is nothing here
        to refuse. The method exists because *some* engine has to be able to
        say no — see ``WholeArtifactRuntimeProjection.validate_plan``.
        """

    async def apply(
        self,
        *,
        plan: ResolvedSkillPlan,
        scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        service_factory: RuntimeServiceFactoryBoundary,
    ) -> RuntimeProjectionResult:
        """Write the halves ``scope`` declares, and only those.

        A mutation that changed one half has nothing to say to the other, and
        both halves are whole-snapshot writes: re-sending the unchanged one
        costs a round trip to restate what is already there.
        ``ProjectionScope.everything()`` sets both flags, so a caller with
        nothing to declare still projects both.

        ``retired_mappings`` overrides the Skill flag rather than trusting it:
        those retirements were computed from the actual before/after
        snapshots, so they are evidence that Skills moved. Skipping them would
        strand a published mapping the desired state no longer holds.
        """
        results: list[RuntimeProjectionResult] = []
        if scope.skills or retired_mappings:
            try:
                results.append(
                    await self._skill_delivery.deliver(
                        plan=plan,
                        retired_mappings=retired_mappings,
                        service_factory=service_factory,
                    )
                )
            except DeviceOfflineError:
                if str(plan.bot.get("bot_type") or "").lower() != "desktop":
                    logger.warning(
                        "[PerDomainRuntimeProjection] runtime device has no "
                        "active instance bot_id=%s engine=%s bot_type=%s",
                        plan.bot_id,
                        plan.engine,
                        plan.bot.get("bot_type"),
                    )
                    results.append(
                        RuntimeProjectionResult.pending(
                            code="SKILL_RUNTIME_UNAVAILABLE",
                            reason="Skill 运行环境当前不可连接，能力状态已保存但尚未同步",
                        )
                    )
                else:
                    log = (
                        logger.info
                        if str(plan.bot.get("status") or "").upper() == "OFFLINE"
                        else logger.warning
                    )
                    log(
                        "[PerDomainRuntimeProjection] Desktop device offline "
                        "bot_id=%s engine=%s db_status=%s",
                        plan.bot_id,
                        plan.engine,
                        plan.bot.get("status"),
                    )
                    results.append(
                        RuntimeProjectionResult.pending(
                            code="DESKTOP_DEVICE_OFFLINE",
                            reason="Desktop 设备当前离线，能力状态已保存，将在设备上线后自动同步",
                            suggested_action="请启动或重新连接 Desktop 客户端。",
                        )
                    )
            except DeviceConnectionUnavailableError:
                logger.warning(
                    "[PerDomainRuntimeProjection] transient device connection "
                    "failure bot_id=%s engine=%s",
                    plan.bot_id,
                    plan.engine,
                )
                results.append(
                    RuntimeProjectionResult.pending(
                        code="SKILL_RUNTIME_UNAVAILABLE",
                        reason="Skill 运行环境当前不可连接，能力状态已保存但尚未同步",
                    )
                )
            except Exception:
                logger.exception(
                    "[PerDomainRuntimeProjection] skill projection unavailable "
                    "bot_id=%s engine=%s",
                    plan.bot_id,
                    plan.engine,
                )
                results.append(
                    RuntimeProjectionResult.pending(
                        code="SKILL_RUNTIME_UNAVAILABLE",
                        reason="Skill 运行环境当前不可连接，能力状态已保存但尚未同步",
                    )
                )
        else:
            logger.info(
                "[PerDomainRuntimeProjection] Skill projection skipped, scope "
                "declares no Skill change: bot_id=%s, engine=%s",
                plan.bot_id,
                plan.engine,
            )
            results.append(
                RuntimeProjectionResult.skipped(reason="SKILL_SCOPE_UNCHANGED")
            )
        if scope.mcp:
            if not isinstance(plan, ResolvedCapabilityPlan):
                results.append(
                    RuntimeProjectionResult.pending(
                        code="MCP_RUNTIME_PLAN_UNAVAILABLE",
                        reason="MCP 运行时配置暂不可用，能力状态已保存但尚未同步",
                    )
                )
            else:
                try:
                    results.append(
                        await self._apply_mcp_projection(plan=plan, scope=scope)
                    )
                except Exception:
                    logger.exception(
                        "[PerDomainRuntimeProjection] MCP projection unavailable "
                        "bot_id=%s engine=%s",
                        plan.bot_id,
                        plan.engine,
                    )
                    results.append(
                        RuntimeProjectionResult.pending(
                            code="MCP_RUNTIME_UNAVAILABLE",
                            reason="MCP 运行环境当前不可连接，能力状态已保存但尚未同步",
                        )
                    )
        else:
            logger.info(
                "[PerDomainRuntimeProjection] MCP projection skipped, scope "
                "declares no MCP change: bot_id=%s, engine=%s",
                plan.bot_id,
                plan.engine,
            )
            results.append(
                RuntimeProjectionResult.skipped(reason="MCP_SCOPE_UNCHANGED")
            )
        return RuntimeProjectionResult.combine(*results)

    async def _apply_mcp_projection(
        self,
        *,
        plan: ResolvedCapabilityPlan,
        scope: ProjectionScope,
    ) -> RuntimeProjectionResult:
        codes = set(plan.projection.mcp_server_codes)
        if scope.claim_all_mcp:
            # The device-activated listener, and only it. A freshly active
            # container holds no MCP configuration, so there is nothing to
            # refresh against — the allow-list alone would whitelist every MCP
            # with no endpoint or api_key behind it. The caller cannot name
            # the codes itself: the projected set is only known here, after
            # the plan resolves. Nothing is released on this path, so it can
            # only ever add configuration.
            claimed, released = frozenset(codes), frozenset()
        else:
            # A guard, never a source. ``claimed`` cannot grow past what the
            # mutation declared, so a single-MCP add stays a single device
            # write. ``- codes`` stops a release from deleting a code the
            # default policy or a Skill dependency still supplies without any
            # Set claiming it.
            claimed = scope.claimed_mcp & codes
            released = scope.released_mcp - codes
            if claimed != scope.claimed_mcp or released != scope.released_mcp:
                logger.info(
                    "[PerDomainRuntimeProjection] MCP scope guarded against the "
                    "projected set: bot_id=%s, claimed %s->%s, released %s->%s",
                    plan.bot_id,
                    sorted(scope.claimed_mcp),
                    sorted(claimed),
                    sorted(scope.released_mcp),
                    sorted(released),
                )
        # One call, not two: how many device writes an MCP projection takes,
        # and in what order, is decided by the service that owns device
        # resolution. See ``SkillSetService.project_mcps``.
        if not await plan.service.project_mcps(
            claimed=claimed, released=released, declared=codes
        ):
            return RuntimeProjectionResult.pending(
                code="MCP_RUNTIME_UNAVAILABLE",
                reason="MCP 运行环境当前不可连接，能力状态已保存但尚未同步",
            )
        return RuntimeProjectionResult.converged(
            components={"mcp": RuntimeProjectionStatus.CONVERGED}
        )


__all__ = ["PerDomainRuntimeProjection"]
