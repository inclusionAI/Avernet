"""Stage-parameterized release runner.

The verify and online release paths — first-release (create a new BaaS bot) and
upgrade (reuse an existing one) — were four near-identical methods differing only
by a handful of stage-keyed values. They collapse here into one
:meth:`ReleaseStageRunner.first_release` and one :meth:`ReleaseStageRunner.upgrade_release`,
selected by a :class:`StageSpec`. Behavior is preserved verbatim, including the
verify-first-release quirk of not sending a ``version`` to ``release_async``.

The runner operates through the ``PublishFlowService`` facade (``flow``) for the
shared helpers (``_record_release_result`` / ``_approve_baas_publish`` /
``_refresh_publish_handle`` / ext helpers / provider seam), so those remain a
single implementation and stay interceptable by tests.
"""
from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus
from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()


@dataclass(frozen=True)
class StageSpec:
    """The stage-keyed values that distinguish a verify release from an online one."""

    stage: PublishStage
    source_status: PublishStatus
    target_status: PublishStatus
    upgrade_request_label: str
    first_release_message: str
    upgrade_message: str
    # Historical quirk preserved: the verify first-release does NOT pass a
    # ``version`` to ``release_async`` (defaults to "1"); the online one does.
    first_release_passes_version: bool


VERIFY_SPEC = StageSpec(
    stage=PublishStage.VERIFY,
    source_status=PublishStatus.BUILT,
    target_status=PublishStatus.VALIDATE_PUB,
    upgrade_request_label="upgrade_verify",
    first_release_message="已发布到验证环境",
    upgrade_message="已升级发布到验证环境",
    first_release_passes_version=False,
)

ONLINE_SPEC = StageSpec(
    stage=PublishStage.ONLINE,
    source_status=PublishStatus.VALIDATING,
    target_status=PublishStatus.ONLINE_PUB,
    upgrade_request_label="upgrade_online",
    first_release_message="发布已提交",
    upgrade_message="升级发布已提交",
    first_release_passes_version=True,
)


class ReleaseStageRunner:
    """Run a release for one stage — first-release or upgrade — via the facade."""

    def __init__(self, flow) -> None:
        self._flow = flow

    async def first_release(
        self,
        spec: StageSpec,
        publish_record: BotPublishRecord,
        operator: str,
        migration_path: str,
        bot: dict,
    ) -> PublishFlowResult:
        """首次发布（创建新 Bot）。teclaw 走非挂载投递冻结产物，由 release_async 按
        容器 provider 路由。投递前把 engine_ext.stage 重盖为该阶段并叠加本阶段的
        DingTalk 渠道 engine_overrides；ARCA 无 config_artifact 时均 no-op。"""
        flow = self._flow
        publish_id = publish_record.id
        owner_id = flow._get_owner_id(publish_record)

        overrides = flow._stage_overrides(publish_record, spec.stage)
        config_artifact = flow._artifact_for_stage(
            (publish_record.ext or {}).get("config_artifact"),
            spec.stage,
            overrides,
        )
        release_kwargs = dict(
            bot=bot,
            user_id=owner_id,
            migration_path=migration_path,
            device_count=1,
            publish_stage=spec.stage,
            config_artifact=config_artifact,
        )
        if spec.first_release_passes_version:
            release_kwargs["version"] = f"{publish_record.version}"
        release_result = await flow._build_service.release_async(**release_kwargs)

        bot_uuid = release_result.get("bot_uuid")
        baas_publish_id = release_result.get("publish_id")
        if not bot_uuid:
            raise PublishFlowServiceError("BaaS 层未返回 bot_uuid")

        ext = publish_record.ext or {}
        binding_id, ext = flow._record_release_result(
            publish_id=publish_id,
            bot=bot,
            bot_uuid=bot_uuid,
            baas_publish_id=baas_publish_id,
            operator=operator,
            ext=ext,
            stage=spec.stage,
            source_status=spec.source_status,
            target_status=spec.target_status,
            engine_overrides=overrides,
        )

        logger.info(
            "[ReleaseStageRunner.first_release] %s release completed: bot_uuid=%s",
            spec.stage.value, bot_uuid,
        )

        return PublishFlowResult(
            publish_id=publish_id,
            status=spec.target_status,
            message=spec.first_release_message,
            action="process",
            bot_uuid=bot_uuid,
            baas_publish_id=str(baas_publish_id) if baas_publish_id else None,
            device_binding_id=binding_id,
        )

    async def upgrade_release(
        self,
        spec: StageSpec,
        publish_record: BotPublishRecord,
        operator: str,
        migration_path: str,
        bot: dict,
        *,
        bot_uuid: str,
        existing_binding_id: int,
        fallback,
    ) -> PublishFlowResult:
        """升级发布（复用已有 Bot ``bot_uuid`` / ``existing_binding_id``）。BaaS 返回
        BOT_NOT_FOUND 时回退到 ``fallback``（对应阶段的首次发布）。"""
        flow = self._flow
        publish_id = publish_record.id
        version = f"{publish_record.version}"
        owner_id = flow._get_owner_id(publish_record)

        overrides = flow._stage_overrides(publish_record, spec.stage)
        config_artifact = flow._artifact_for_stage(
            (publish_record.ext or {}).get("config_artifact"),
            spec.stage,
            overrides,
        )
        upgrade_result = await flow._build_service.upgrade_async(
            bot_uuid=bot_uuid,
            bot=bot,
            user_id=owner_id,
            device_count=1,
            migration_path=migration_path,
            publish_stage=spec.stage,
            version=version,
            config_artifact=config_artifact,
        )

        if (
            upgrade_result.get("success") is False
            and upgrade_result.get("error_code") == "BOT_NOT_FOUND"
        ):
            logger.warning(
                "[ReleaseStageRunner.upgrade_release] %s upgrade target bot not "
                "found, fallback to first release: publish_id=%s, bot_uuid=%s",
                spec.stage.value, publish_id, bot_uuid,
            )
            return await fallback(
                publish_record=publish_record,
                operator=operator,
                migration_path=migration_path,
                bot=bot,
            )

        baas_publish_id = upgrade_result.get("publish_id")
        if not baas_publish_id:
            raise PublishFlowServiceError("BaaS 层升级未返回 publish_id")

        # 复用已有 binding，更新 ext（stamp stage、存储渠道、刷新 teclaw 读句柄）。
        ext = flow._get_latest_ext(publish_id)
        ext.setdefault("binding", {})[spec.stage.value] = existing_binding_id
        ext.setdefault("publish", {})[spec.stage.value] = baas_publish_id
        flow._restamp_ext_artifact(ext, spec.stage)
        flow._store_stage_overrides(ext, spec.stage, overrides)
        flow._refresh_publish_handle(existing_binding_id, baas_publish_id)
        flow._update_publish_status(
            publish_id=publish_id,
            target_status=spec.target_status,
            source_status=spec.source_status,
            ext=ext,
        )

        request_id = flow._build_service.generate_request_id(
            bot=bot,
            publish_stage=spec.upgrade_request_label,
        )
        approved = flow._approve_baas_publish(
            baas_publish_id=baas_publish_id,
            operator=operator,
            stage=spec.stage,
            request_id=request_id,
        )
        if approved is True:
            # Provider-specific post-upgrade refresh (teclaw re-pushes the MCP
            # outbound rule; ARCA/baas refresh via the startup callback → no-op).
            flow._provider_behavior(bot).refresh_after_upgrade(
                bot_uuid=bot_uuid,
                bot=bot,
            )

        logger.info(
            "[ReleaseStageRunner.upgrade_release] %s upgrade completed: "
            "bot_uuid=%s, baas_publish_id=%s",
            spec.stage.value, bot_uuid, baas_publish_id,
        )

        return PublishFlowResult(
            publish_id=publish_id,
            status=spec.target_status,
            message=spec.upgrade_message,
            action="process",
            bot_uuid=bot_uuid,
            baas_publish_id=str(baas_publish_id) if baas_publish_id else None,
            device_binding_id=existing_binding_id,
        )
