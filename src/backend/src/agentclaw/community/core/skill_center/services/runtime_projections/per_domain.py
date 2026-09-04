"""Runtime projection for engines whose halves have separate endpoints."""

from __future__ import annotations

from collections.abc import Sequence

from agentclaw.community.core.skill_center.errors import (
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    EngineRuntimeProjection,
    ProjectionScope,
    ResolvedCapabilityPlan,
    ResolvedSkillPlan,
    RuntimeProjectionIssue,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skill_center.runtime_resolver import RuntimeSkillProjection
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    MAPPING_CONTRACT_V3,
    mapping_contract_for,
)
from agentclaw.community.core.skills_pool.models import (
    MappingApplyMode,
    MappingProjectionStatus,
    MappingPublishResult,
    MappingVerificationResult,
    PoolSkillMapping,
    RegisteredSkillAsset,
    SkillMappingSourceLayout,
)
from agentclaw.community.core.skills_pool.ports import SkillsPoolRuntimeProtocol
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    runtime_uses_pool_paths,
)
from agentclaw.community.core.workspace.skill_layout import runtime_layout_engine_for_bot
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
        pool_runtime: SkillsPoolRuntimeProtocol,
        pool_layouts: SkillsPoolLayoutRepositoryProtocol,
    ) -> None:
        self._pool_runtime = pool_runtime
        self._pool_layouts = pool_layouts

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
                    await self._apply_skill_projection(
                        plan=plan, retired_mappings=retired_mappings
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
                plan.bot_id, plan.engine,
            )
            results.append(RuntimeProjectionResult.skipped(reason="SKILL_SCOPE_UNCHANGED"))
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
                    results.append(await self._apply_mcp_projection(plan=plan, scope=scope))
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
                plan.bot_id, plan.engine,
            )
            results.append(RuntimeProjectionResult.skipped(reason="MCP_SCOPE_UNCHANGED"))
        return RuntimeProjectionResult.combine(*results)

    async def _apply_skill_projection(
        self,
        *,
        plan: ResolvedSkillPlan,
        retired_mappings: Sequence[PoolSkillMapping],
    ) -> RuntimeProjectionResult:
        mappings = list(plan.projection.skill_mappings)
        retired = list(retired_mappings)
        bot = plan.bot

        scope = BotSkillLayoutScope(
            env=str(bot["env"]),
            entity_id=str(bot.get("entity_id") or plan.owner_id),
            bot_id=plan.bot_id,
        )
        layout_state = self._pool_layouts.get(scope)
        pool_owns_runtime = layout_state is not None and runtime_uses_pool_paths(
            layout_state
        )
        if (
            pool_owns_runtime
            or any(
                mapping.corpus in {"repo", "center"}
                for mapping in [*mappings, *retired]
            )
            or retired
        ):
            return await self._apply_pool_mappings(
                bot_id=plan.bot_id,
                owner_id=plan.owner_id,
                layout_engine=runtime_layout_engine_for_bot(bot),
                mappings=mappings,
                retired_mappings=retired,
                source_layout=(
                    SkillMappingSourceLayout.POOL
                    if pool_owns_runtime
                    else SkillMappingSourceLayout.LEGACY
                ),
            )
        # A plain await: the blocking device call behind ``project_skills``
        # is dispatched off the event loop by that method itself, so this
        # legacy path gets the guarantee without restating it.
        elif not await plan.service.project_skills(
            desired_skills=self._desired_skills(plan.projection),
        ):
            return RuntimeProjectionResult.pending(
                code="SKILL_RUNTIME_UNAVAILABLE",
                reason="Skill 运行环境当前不可连接，能力状态已保存但尚未同步",
            )
        return RuntimeProjectionResult.converged(
            components={"skills": RuntimeProjectionStatus.CONVERGED}
        )

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
                    sorted(scope.claimed_mcp), sorted(claimed),
                    sorted(scope.released_mcp), sorted(released),
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

    async def _apply_pool_mappings(
        self,
        *,
        bot_id: str,
        owner_id: str,
        layout_engine: str,
        mappings: list[PoolSkillMapping],
        retired_mappings: list[PoolSkillMapping],
        source_layout: SkillMappingSourceLayout,
    ) -> RuntimeProjectionResult:
        try:
            contract_mappings = [*mappings, *retired_mappings]
            supported_versions: object = None
            if any(mapping.corpus == "center" for mapping in contract_mappings):
                probe = await self._pool_runtime.probe(
                    bot_id=bot_id,
                    user_id=owner_id,
                    engine=layout_engine,
                )
                supported_versions = probe.evidence.get(
                    "supported_mapping_contract_versions"
                )
                center_mount = probe.evidence.get("center_mount")
                if (
                    isinstance(center_mount, dict)
                    and center_mount.get("restart_required") is True
                    and (
                        not isinstance(supported_versions, list)
                        or MAPPING_CONTRACT_V3 not in supported_versions
                    )
                ):
                    return RuntimeProjectionResult.pending(
                        code="CENTER_RUNTIME_RESTART_REQUIRED",
                        reason="Bot 尚未加载 Skill Center 目录，请重启 Bot 后重试",
                    )
            contract = mapping_contract_for(contract_mappings, supported_versions)
            raw_published = await self._pool_runtime.publish_mappings(
                bot_id=bot_id,
                user_id=owner_id,
                mappings=mappings,
                retired_mappings=retired_mappings,
                source_layout=source_layout,
                mapping_contract_version=contract,
                apply_mode=MappingApplyMode.BEST_EFFORT,
            )
            raw_verified = await self._pool_runtime.verify_mappings(
                bot_id=bot_id,
                user_id=owner_id,
                mappings=mappings,
                retired_mappings=retired_mappings,
                source_layout=source_layout,
                mapping_contract_version=contract,
                apply_mode=MappingApplyMode.BEST_EFFORT,
            )
            published = self._publish_result(raw_published)
            verified = self._verification_result(raw_verified)
        except Exception as exc:
            raise SkillSetRuntimeReconcileError() from exc
        return self._mapping_result(
            mappings=mappings,
            published=published,
            verified=verified,
        )

    @staticmethod
    def _mapping_result(
        *,
        mappings: list[PoolSkillMapping],
        published: MappingPublishResult,
        verified: MappingVerificationResult,
    ) -> RuntimeProjectionResult:
        published_by_target = {item.target: item for item in published.items}
        verified_by_target = {item.target: item for item in verified.items}
        # Verify observes the filesystem *after* publish. If a transient
        # publish I/O error left no target at all, verify can only call it
        # TARGET_NOT_SYMLINK; retain the publisher's retryable PENDING rather
        # than turning a recoverable outage into a non-retryable degradation.
        item_by_target = {
            target: (
                published_item
                if published_item.status is MappingProjectionStatus.PENDING
                else verified_by_target.get(target, published_item)
            )
            for target, published_item in published_by_target.items()
        }
        item_by_target.update(
            {
                target: verified_item
                for target, verified_item in verified_by_target.items()
                if target not in item_by_target
            }
        )
        issues: list[RuntimeProjectionIssue] = []
        mapping_by_name = {mapping.link_name: mapping for mapping in mappings}
        for item in item_by_target.values():
            if item.status is MappingProjectionStatus.CONVERGED:
                continue
            logical_name = item.target.rsplit("/", 1)[-1]
            mapping = mapping_by_name.get(logical_name)
            code, reason, suggested_action, observed, expected = (
                PerDomainRuntimeProjection._mapping_message(item.code)
            )
            issues.append(
                RuntimeProjectionIssue(
                    resource_type="SKILL",
                    resource_id=None,
                    name=logical_name,
                    corpus=mapping.corpus.upper() if mapping is not None else None,
                    code=code,
                    reason=reason,
                    status=(
                        RuntimeProjectionStatus.PENDING
                        if item.status is MappingProjectionStatus.PENDING
                        else RuntimeProjectionStatus.DEGRADED
                    ),
                    retryable=item.retryable,
                    observed_entry_type=observed,
                    expected_entry_type=expected,
                    logical_location=f"active-skills/{logical_name}",
                    suggested_action=suggested_action,
                )
            )
        item_statuses = {item.status for item in item_by_target.values()}
        status = (
            RuntimeProjectionStatus.DEGRADED
            if MappingProjectionStatus.DEGRADED in item_statuses
            else (
                RuntimeProjectionStatus.PENDING
                if MappingProjectionStatus.PENDING in item_statuses
                else RuntimeProjectionStatus.CONVERGED
            )
        )
        if not issues and status is not RuntimeProjectionStatus.CONVERGED:
            issues.append(
                RuntimeProjectionIssue(
                    resource_type="RUNTIME",
                    code="SKILL_MAPPING_RUNTIME_UNAVAILABLE",
                    reason="Skill 运行环境当前不可连接，能力状态已保存但尚未同步",
                    status=status,
                    retryable=status is RuntimeProjectionStatus.PENDING,
                    suggested_action=(
                        "Bot 当前未完成运行时同步。请稍后再次保存能力集；若持续失败，"
                        "请联系管理员并提供错误详情。"
                    ),
                )
            )
        return RuntimeProjectionResult(
            status=status,
            components={"skills": status},
            issues=tuple(issues),
        )

    @staticmethod
    def _publish_result(value: object) -> MappingPublishResult:
        if isinstance(value, MappingPublishResult):
            return value
        return MappingPublishResult(
            published=bool(value),
            status=(
                MappingProjectionStatus.CONVERGED
                if value
                else MappingProjectionStatus.PENDING
            ),
        )

    @staticmethod
    def _verification_result(value: object) -> MappingVerificationResult:
        if isinstance(value, MappingVerificationResult):
            return value
        return MappingVerificationResult(
            valid=bool(value),
            status=(
                MappingProjectionStatus.CONVERGED
                if value
                else MappingProjectionStatus.PENDING
            ),
        )

    @staticmethod
    def _mapping_message(
        code: str | None,
    ) -> tuple[str, str, str, str | None, str | None]:
        messages = {
            "MANAGED_SOURCE_MISSING": (
                "MANAGED_SOURCE_MISSING",
                "Skill 的源文件已被删除或移动，平台已保留目标软链",
                "该技能的内容暂时不可用。请重新同步或重新添加该技能后，再保存能力集。",
                "DANGLING_SYMLINK",
                "SYMLINK",
            ),
            "UNMANAGED_ACTIVE_ENTRY_RETAINED": (
                "UNMANAGED_ACTIVE_ENTRY_RETAINED",
                "Bot 生效目录中存在同名实体目录，平台没有覆盖或删除该目录",
                "该技能已在 Bot 内被手动维护。为避免覆盖现有内容，平台没有替换它。"
                "请联系 Bot 管理员确认处理后，再保存能力集。",
                "DIRECTORY",
                "SYMLINK",
            ),
            "EXTERNAL_ACTIVE_ENTRY_RETAINED": (
                "EXTERNAL_ACTIVE_ENTRY_RETAINED",
                "同名软链指向平台管理目录之外，平台没有修改该软链",
                "该技能当前由其他配置管理，平台没有修改它。请联系 Bot 管理员确认"
                "是否交由平台管理后，再保存能力集。",
                "EXTERNAL_SYMLINK",
                "SYMLINK",
            ),
        }
        return messages.get(
            code or "",
            (
                code or "SKILL_MAPPING_DEGRADED",
                "Skill 运行时投影尚未完成",
                "部分技能未完成运行时同步。请稍后再次保存能力集；若持续失败，"
                "请联系管理员并提供错误详情。",
                None,
                None,
            ),
        )

    @staticmethod
    def _desired_skills(
        projection: RuntimeSkillProjection,
    ) -> list[dict[str, str | None]]:
        return [
            {
                "id": str(asset.skill_id),
                "name": asset.name,
                "git_path": asset.git_path,
                "skill_uuid": asset.skill_uuid,
                "sc_version_number": asset.sc_version_number,
            }
            for asset in projection.skill_assets
        ]


__all__ = ["PerDomainRuntimeProjection"]
