"""Steady-state filesystem Skill delivery and compatibility routing."""

from __future__ import annotations

from collections.abc import Sequence

from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetRuntimeReconcileError,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ResolvedSkillPlan,
    RuntimeProjectionIssue,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
    RuntimeServiceFactoryBoundary,
)
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeSkillProjection,
)
from agentclaw.community.core.skills_pool.mapping_intent import (
    MAPPING_CONTRACT_V3,
    mapping_contract_for,
)
from agentclaw.community.core.skills_pool.models import (
    MappingApplyMode,
    MappingApplyResult,
    MappingProjectionStatus,
    MappingPublishResult,
    MappingVerificationResult,
    PoolSkillMapping,
    SkillMappingSourceLayout,
)
from agentclaw.community.core.skills_pool.ports import SkillsPoolRuntimeProtocol
from agentclaw.community.core.skills_pool.runtime import LegacyMappingApplyRequired
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    runtime_uses_pool_paths,
)
from agentclaw.community.core.workspace.skill_layout import (
    runtime_layout_engine_for_bot,
)


class SkillRuntimeDelivery:
    """Deliver one resolved Skill plan without exposing layout compatibility.

    This is the single steady-state boundary for filesystem engines. It keeps
    the historical DeviceSync route for Legacy Local-only snapshots and owns
    the logical Mapping route for Pool, Repo, Center, and explicit retirement.
    Pool migration and recovery continue to consume ``SkillsPoolRuntimeProtocol``
    directly because their strict data-safety semantics are different.
    """

    def __init__(
        self,
        *,
        pool_runtime: SkillsPoolRuntimeProtocol,
        pool_layouts: SkillsPoolLayoutRepositoryProtocol,
    ) -> None:
        self._pool_runtime = pool_runtime
        self._pool_layouts = pool_layouts

    async def deliver(
        self,
        *,
        plan: ResolvedSkillPlan,
        retired_mappings: Sequence[PoolSkillMapping] = (),
        service_factory: RuntimeServiceFactoryBoundary,
    ) -> RuntimeProjectionResult:
        """Deliver the already-resolved plan and interpret its Skill result."""
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
        uses_legacy_mapping = (
            pool_owns_runtime
            or any(
                mapping.corpus in {"repo", "center"}
                for mapping in [*mappings, *retired]
            )
            or retired
        )
        source_layout = (
            SkillMappingSourceLayout.POOL
            if pool_owns_runtime
            else SkillMappingSourceLayout.LEGACY
        )
        try:
            applied = await self._pool_runtime.apply_mappings(
                bot_id=plan.bot_id,
                user_id=plan.owner_id,
                engine=runtime_layout_engine_for_bot(bot),
                mappings=mappings,
                retired_mappings=retired,
                source_layout=source_layout,
            )
        except LegacyMappingApplyRequired:
            if uses_legacy_mapping:
                return await self._apply_pool_mappings(
                    bot_id=plan.bot_id,
                    owner_id=plan.owner_id,
                    layout_engine=runtime_layout_engine_for_bot(bot),
                    mappings=mappings,
                    retired_mappings=retired,
                    source_layout=source_layout,
                )
            return await self._apply_legacy_device_sync(plan, service_factory)
        return self._logical_mapping_result(
            plan=plan,
            retired_mappings=retired,
            applied=applied,
        )

    async def _apply_legacy_device_sync(
        self,
        plan: ResolvedSkillPlan,
        service_factory: RuntimeServiceFactoryBoundary,
    ) -> RuntimeProjectionResult:
        service = service_factory.create(
            user_id=plan.owner_id,
            entity_id=str(plan.bot.get("entity_id") or plan.owner_id),
            bot_id=plan.bot_id,
            engine_type=plan.engine,
            entity_type=plan.bot.get("entity_type") or "staff",
        )
        if not await service.project_skills(
            desired_skills=self._desired_skills(plan.projection),
        ):
            return RuntimeProjectionResult.pending(
                code="SKILL_RUNTIME_UNAVAILABLE",
                reason="Skill 运行环境当前不可连接，能力状态已保存但尚未同步",
            )
        return RuntimeProjectionResult.converged(
            components={"skills": RuntimeProjectionStatus.CONVERGED}
        )

    @staticmethod
    def _logical_mapping_result(
        *,
        plan: ResolvedSkillPlan,
        retired_mappings: Sequence[PoolSkillMapping],
        applied: MappingApplyResult,
    ) -> RuntimeProjectionResult:
        expected_apply = set(plan.projection.skill_mappings)
        desired_names = {mapping.link_name for mapping in expected_apply}
        expected_retire = {
            mapping
            for mapping in retired_mappings
            if mapping.link_name not in desired_names
        }
        seen_apply: dict[PoolSkillMapping, MappingProjectionStatus] = {}
        seen_retire: dict[PoolSkillMapping, MappingProjectionStatus] = {}
        issues: list[RuntimeProjectionIssue] = []
        asset_by_mapping = dict(
            zip(
                plan.projection.skill_mappings,
                plan.projection.skill_assets,
                strict=True,
            )
        )
        all_items = [*applied.items, *applied.issues]
        invalid = False
        for item in all_items:
            mapping = item.mapping
            if item.action == "APPLY" and mapping is not None:
                if mapping not in expected_apply or mapping in seen_apply:
                    invalid = True
                seen_apply[mapping] = item.status
            elif item.action == "RETIRE" and mapping is not None:
                if mapping not in expected_retire or mapping in seen_retire:
                    invalid = True
                seen_retire[mapping] = item.status
            elif item.action != "RUNTIME" or mapping is not None:
                invalid = True
            if item.status is MappingProjectionStatus.CONVERGED:
                continue
            asset = asset_by_mapping.get(mapping) if mapping is not None else None
            code, reason, suggested_action, observed, expected = (
                SkillRuntimeDelivery._mapping_message(item.code)
            )
            issues.append(
                RuntimeProjectionIssue(
                    resource_type="SKILL" if mapping is not None else "RUNTIME",
                    resource_id=str(asset.skill_id) if asset is not None else None,
                    name=(asset.name if asset is not None else mapping.link_name if mapping else None),
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
                    logical_location=(
                        f"active-skills/{mapping.link_name}" if mapping else None
                    ),
                    suggested_action=suggested_action,
                )
            )
        missing = (expected_apply - set(seen_apply)) | (
            expected_retire - set(seen_retire)
        )
        if missing and applied.status is MappingProjectionStatus.CONVERGED:
            invalid = True
        item_statuses = {item.status for item in all_items}
        severity = {
            MappingProjectionStatus.CONVERGED: 0,
            MappingProjectionStatus.PENDING: 1,
            MappingProjectionStatus.DEGRADED: 2,
        }
        if item_statuses and severity[applied.status] < max(
            severity[item_status] for item_status in item_statuses
        ):
            invalid = True
        if invalid or MappingProjectionStatus.DEGRADED in item_statuses:
            status = RuntimeProjectionStatus.DEGRADED
        elif (
            applied.status is MappingProjectionStatus.PENDING
            or MappingProjectionStatus.PENDING in item_statuses
        ):
            status = RuntimeProjectionStatus.PENDING
        else:
            status = RuntimeProjectionStatus.CONVERGED
        if invalid:
            issues.append(
                RuntimeProjectionIssue(
                    resource_type="RUNTIME",
                    code="SKILL_MAPPING_RESULT_INVALID",
                    reason="Skill 运行时返回的逻辑映射结果不完整或相互矛盾",
                    status=RuntimeProjectionStatus.DEGRADED,
                    retryable=False,
                    suggested_action="请联系管理员并提供错误详情。",
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
                    suggested_action="请稍后再次保存能力集；若持续失败，请联系管理员。",
                )
            )
        return RuntimeProjectionResult(
            status=status,
            components={"skills": status},
            issues=tuple(issues),
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
        # Verify observes the filesystem after publish. Preserve a retryable
        # publish outage when verify can only observe the missing target.
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
                SkillRuntimeDelivery._mapping_message(item.code)
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
        if not published.items:
            item_statuses.add(published.status)
        if not verified.items:
            item_statuses.add(verified.status)
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


__all__ = ["SkillRuntimeDelivery"]
