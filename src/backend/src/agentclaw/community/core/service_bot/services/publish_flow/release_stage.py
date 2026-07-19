"""Stage-parameterized release runner.

The verify and online release paths — first-release (create a new BaaS bot) and
upgrade (reuse an existing one) — were four near-identical methods differing only
by a handful of stage-keyed values. They collapse here into one
:meth:`ReleaseStageRunner.first_release` and one :meth:`ReleaseStageRunner.upgrade_release`,
selected by a :class:`StageSpec`. Behavior is preserved verbatim, including the
verify-first-release quirk of not sending a ``version`` to ``release_async``.

The runner takes its real dependencies explicitly (ext/state helpers, the build
service, provider resolution) instead of reaching into ``PublishFlowService``
private members. The release-record writes that live on the facade's mixins
(create the device binding, record the publish ext, approve the BaaS workflow,
refresh the read handle) are consumed through the narrow public
:class:`ReleaseRecordOps` protocol — the facade satisfies it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishOperationKind,
    PublishStatus,
)
from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.service_bot.services.bot_build_service import BotBuildService
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.services.publish_flow.ext_state import (
    PublishExtState,
)
from agentclaw.community.core.service_bot.services.publish_flow.provider_behavior import (
    ProviderBehavior,
    ProviderBehaviorRouter,
)
from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
    PublishOperationRunner,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()


class ReleaseRecordOps(Protocol):
    """The release-record operations the runner needs from the flow facade.

    These are public, multi-consumer domain ops that live on the facade's mixins
    (device-binding insert, publish-ext write, BaaS approve, read-handle
    refresh); the runner consumes them through this narrow protocol instead of
    holding the whole facade.
    """

    def create_release_binding(
        self, *, bot: dict, bot_uuid: str, baas_publish_id: int, operator: str
    ) -> int: ...

    def record_release_ext(
        self,
        *,
        publish_id: int,
        bot: dict,
        stage: PublishStage,
        binding_id: int,
        baas_publish_id: int,
        source_status: PublishStatus,
        target_status: PublishStatus,
        engine_overrides: dict | None = None,
    ) -> dict: ...

    def refresh_publish_handle(self, binding_id, publish_id) -> None: ...


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
    first_release_message="Released to the verify environment",
    upgrade_message="Upgraded and released to the verify environment",
    first_release_passes_version=False,
)

# The online release runs *within* ONLINE_PUB: the user-driven ``process`` owns the
# VALIDATING → ONLINE_PUB advance, so the runner records the release into ext under
# a same-status (ONLINE_PUB → ONLINE_PUB) optimistic-locked write rather than
# advancing the status itself. The verify release, by contrast, still owns its
# BUILT → VALIDATE_PUB advance (that transition is the whole verify flow's infra).
ONLINE_SPEC = StageSpec(
    stage=PublishStage.ONLINE,
    source_status=PublishStatus.ONLINE_PUB,
    target_status=PublishStatus.ONLINE_PUB,
    upgrade_request_label="upgrade_online",
    first_release_message="Publish submitted",
    upgrade_message="Upgrade publish submitted",
    first_release_passes_version=True,
)


class _BotNotFoundError(Exception):
    """Internal signal: BaaS upgrade returned BOT_NOT_FOUND — abandon the upgrade
    op and fall back to a first release."""


class ReleaseStageRunner:
    """Run a release for one stage — first-release or upgrade."""

    def __init__(
        self,
        *,
        ext_state: PublishExtState,
        build_service: BotBuildService,
        baas_service: BaasService,
        provider_behaviors: ProviderBehaviorRouter,
        ops: ReleaseRecordOps,
        operation_runner: PublishOperationRunner,
    ) -> None:
        self._ext_state = ext_state
        self._build_service = build_service
        self._baas_service = baas_service
        self._provider_behaviors = provider_behaviors
        self._ops = ops
        self._operation_runner = operation_runner

    def _provider_behavior(self, bot: dict) -> ProviderBehavior:
        """The :class:`ProviderBehavior` for ``bot``'s container."""
        return self._provider_behaviors.resolve(
            self._baas_service.resolve_container_provider(bot)
        )

    async def first_release(
        self,
        spec: StageSpec,
        publish_record: BotPublishRecord,
        operator: str,
        migration_path: str,
        bot: dict,
    ) -> PublishFlowResult:
        """First release (create a new Bot). teclaw uses non-mounted delivery of the
        frozen artifact, routed by release_async according to the container provider.
        Before delivery, overwrite engine_ext.stage with this stage and overlay this
        stage's DingTalk channel engine_overrides; both are no-ops for ARCA when there
        is no config_artifact."""
        publish_id = publish_record.id
        owner_id = self._ext_state.owner_id(publish_record)

        # Compose through the single delivery seam (LIVE overrides re-fetch); the raw
        # ext['config_artifact'] is never handed to BaaS. ``overrides`` is the applied
        # overlay, persisted below so a future restart/rollback can reproduce it.
        delivery, overrides = self._ext_state.compose_live(publish_record, spec.stage)

        # Crash-safe issuance (#197): open the ledger op, then acquire the workflow
        # (issue the BaaS create at most once — a resume adopts the in-doubt bot via
        # its returned bot_uuid rather than creating a second one).
        op = self._operation_runner.open_operation(
            publish_id=publish_id,
            kind=PublishOperationKind.FIRST_RELEASE,
            stage=spec.stage.value,
            operator=operator,
        )

        async def _issue():
            release_kwargs = dict(
                bot=bot,
                user_id=owner_id,
                migration_path=migration_path,
                device_count=1,
                publish_stage=spec.stage,
                # TODO(totalfrank): this still isn't fully provider-agnostic — the
                # downstream (release_async / build service) branches on the container
                # provider to interpret config_artifact. Push that decision behind the
                # provider seam in a follow-up; tracked separately.
                delivery=delivery,
            )
            if spec.first_release_passes_version:
                release_kwargs["version"] = f"{publish_record.version}"
            return await self._build_service.release_async(**release_kwargs)

        op = await self._operation_runner.acquire_workflow(op, _issue)
        bot_uuid = op.bot_uuid
        baas_publish_id = op.baas_publish_id
        if not bot_uuid:
            raise PublishFlowServiceError("BaaS layer did not return bot_uuid")
        if not baas_publish_id:
            raise PublishFlowServiceError("BaaS layer did not return publish_id")

        # Two follow-up steps: (1) create the device binding (recorded into the op's
        # result so a re-run reuses it rather than creating a second binding), (2)
        # record the binding/publish refs + provider promotion + status into ext (a
        # source-status-guarded CAS — a no-op on a re-run that already advanced).
        binding_id = (op.result or {}).get("binding_id")
        if binding_id is None:
            binding_id = self._ops.create_release_binding(
                bot=bot,
                bot_uuid=bot_uuid,
                baas_publish_id=baas_publish_id,
                operator=operator,
            )
            op = self._operation_runner.record_step_result(op, {"binding_id": binding_id})
        self._ops.record_release_ext(
            publish_id=publish_id,
            bot=bot,
            stage=spec.stage,
            binding_id=binding_id,
            baas_publish_id=baas_publish_id,
            source_status=spec.source_status,
            target_status=spec.target_status,
            engine_overrides=overrides,
        )
        self._operation_runner.complete_operation(op)

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
        """Upgrade release (reuse an existing Bot ``bot_uuid`` / ``existing_binding_id``).
        When BaaS returns BOT_NOT_FOUND, fall back to ``fallback`` (the first release for
        the corresponding stage)."""
        publish_id = publish_record.id
        version = f"{publish_record.version}"
        owner_id = self._ext_state.owner_id(publish_record)

        # Compose through the single delivery seam (LIVE overrides re-fetch); the raw
        # ext['config_artifact'] is never handed to BaaS. ``overrides`` is the applied
        # overlay, persisted below via the provider's stage-promotion write.
        delivery, overrides = self._ext_state.compose_live(publish_record, spec.stage)

        # Crash-safe issuance (#197): an existing-bot mutation → the runner adopts
        # an in-doubt workflow (queried by bot_uuid) on resume instead of issuing a
        # second upgrade. A BOT_NOT_FOUND from BaaS is signalled out of ``issue`` so
        # the op is abandoned and the first-release fallback opens its own op.
        op = self._operation_runner.open_operation(
            publish_id=publish_id,
            kind=PublishOperationKind.UPGRADE,
            stage=spec.stage.value,
            bot_uuid=bot_uuid,
            operator=operator,
        )

        async def _issue():
            upgrade_result = await self._build_service.upgrade_async(
                bot_uuid=bot_uuid,
                bot=bot,
                user_id=owner_id,
                device_count=1,
                migration_path=migration_path,
                publish_stage=spec.stage,
                version=version,
                delivery=delivery,
            )
            if (
                upgrade_result.get("success") is False
                and upgrade_result.get("error_code") == "BOT_NOT_FOUND"
            ):
                raise _BotNotFoundError()
            return upgrade_result

        try:
            op = await self._operation_runner.acquire_workflow(op, _issue)
        except _BotNotFoundError:
            logger.warning(
                "[ReleaseStageRunner.upgrade_release] %s upgrade target bot not "
                "found, fallback to first release: publish_id=%s, bot_uuid=%s",
                spec.stage.value, publish_id, bot_uuid,
            )
            self._operation_runner.abandon_operation(op, "BOT_NOT_FOUND -> first release")
            return await fallback(
                publish_record=publish_record,
                operator=operator,
                migration_path=migration_path,
                bot=bot,
            )

        baas_publish_id = op.baas_publish_id
        if not baas_publish_id:
            raise PublishFlowServiceError("BaaS layer upgrade did not return publish_id")

        # Reuse the existing binding; update ext (binding/publish refs, provider
        # per-stage promotion state, refresh the teclaw read handle).
        ext = self._ext_state.get_latest_ext(publish_id)
        ext.setdefault("binding", {})[spec.stage.value] = existing_binding_id
        ext.setdefault("publish", {})[spec.stage.value] = baas_publish_id
        self._provider_behavior(bot).persist_stage_promotion(
            ext=ext, stage=spec.stage, engine_overrides=overrides
        )
        self._ops.refresh_publish_handle(existing_binding_id, baas_publish_id)
        self._ext_state.update_status(
            publish_id=publish_id,
            target_status=spec.target_status,
            source_status=spec.source_status,
            ext=ext,
        )
        self._operation_runner.complete_operation(op)

        # All-auto approval (#197): the upgrade workflow is auto-approved
        # server-side — no client approve. The teclaw post-upgrade MCP outbound
        # rule refresh moves to the progress-poll SUCCESS handler
        # (ProgressSyncMixin._handle_sync_success), triggering on observed deploy
        # success rather than an approve return value.
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
