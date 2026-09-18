"""Online release admission and provider-aware deployment orchestration."""
from __future__ import annotations

import copy
from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus
from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult
from agentclaw.community.core.service_bot.services.publish_flow.errors import PublishFlowServiceError
from agentclaw.community.core.service_bot.types import OnlineDeployDecision
from agentclaw.community.log import get_logger

logger = get_logger()


class ReleasePhaseMixin:
    def require_employee_approval(self, publish_id: int) -> None:
        """Admit an already-built version before any online state mutation."""
        if self._employee_publication_provider is not None:
            self._employee_publication_provider().require_approved(publish_id)

    async def execute_release_phase(
        self,
        publish_record: BotPublishRecord,
        operator: str,
    ) -> PublishFlowResult:
        """Run the publish stage.

        Determines first release vs. upgrade release based on last_pub_id:
        - First release: call BotBuildService.release_async() to create a new Bot
        - Upgrade release: call BotBuildService.upgrade_async() to update the existing Bot

        Args:
            publish_record: Publish record
            operator: Operator

        Returns:
            PublishFlowResult: Publish result
        """
        publish_id = publish_record.id
        bot_id = publish_record.source_bot_id
        owner_id = self._get_owner_id(publish_record)
        if self._employee_publication_provider is not None:
            self._employee_publication_provider().require_approved(publish_id)

        # Fetch the build artifact from the ext field. ARCA = migration_path (mounted); teclaw = frozen
        # config_artifact (non-mounted delivery). Neither present → not yet built.
        migration_path = None
        config_artifact = None
        if publish_record.ext:
            migration_path = publish_record.ext.get("migration_path")
            config_artifact = publish_record.ext.get("config_artifact")

        if not migration_path and not config_artifact:
            error_msg = "Build artifact path does not exist, please run the build first"
            logger.error(f"[PublishFlowService]{publish_id}, publish_record={publish_record},  {error_msg}")
            ext = self._get_latest_ext(publish_id)
            expected_ext = copy.deepcopy(ext)
            self._clear_retry_flag(ext)
            ext["error_message"] = error_msg
            # The online release runs within ONLINE_PUB (process owns the
            # VALIDATING -> ONLINE_PUB advance), so failures roll back to ONLINE_PUB.
            ext["source_status"] = PublishStatus.ONLINE_PUB.value
            self._update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.FAILED,
                source_status=PublishStatus.ONLINE_PUB,
                ext=ext,
                expected_ext=expected_ext,
            )
            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.FAILED,
                message=error_msg,
            )

        try:
            logger.info(
                f"[PublishFlowService] Starting release phase for: "
                f"publish_id={publish_id}, bot_id={bot_id}, operator={operator}, owner_id={owner_id}"
            )

            # Fetch Bot info
            bot = self._bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
            if not bot:
                raise PublishFlowServiceError(f"Bot does not exist: {bot_id}")

            # ===== Core logic: provider-aware reuse-vs-recreate decision =====
            # One shared decision (see UpgradeResolutionMixin._decide_online_deploy):
            # reuse the existing bot in place when it is live/rebuildable, else
            # create a fresh one — retiring the superseded bot first when it would
            # otherwise be orphaned (e.g. a teclaw container an UPDATE can't rebuild).
            decision = self._decide_online_deploy(publish_record, bot)
            if decision == OnlineDeployDecision.UPGRADE:
                return await self._execute_upgrade_release(
                    publish_record=publish_record,
                    operator=operator,
                    migration_path=migration_path,
                    bot=bot,
                )
            if decision == OnlineDeployDecision.RETIRE_THEN_FIRST_RELEASE:
                # Retire the superseded bot first (else the fresh create orphans
                # it), then release its now-stale binding so it does not linger
                # ACTIVE pointing at a destroyed bot, then fall into first-release.
                candidate_bot_uuid, candidate_binding_id = (
                    self._resolve_online_reuse_target(publish_record)
                )
                if candidate_bot_uuid:
                    destroy_publish_id = self._build_service.retire_superseded_bot(
                        candidate_bot_uuid, operator=operator
                    )
                    if candidate_binding_id:
                        self._release_binding(
                            candidate_binding_id,
                            destroy_publish_id=destroy_publish_id,
                        )
                return await self._execute_first_release(
                    publish_record=publish_record,
                    operator=operator,
                    migration_path=migration_path,
                    bot=bot,
                )
            if decision == OnlineDeployDecision.FIRST_RELEASE:
                return await self._execute_first_release(
                    publish_record=publish_record,
                    operator=operator,
                    migration_path=migration_path,
                    bot=bot,
                )
            # Exhaustive: every decision is handled explicitly above. A new enum
            # value must fail loudly here rather than silently first-release.
            raise PublishFlowServiceError(
                f"Unhandled online deploy decision: {decision}"
            )

        except Exception as e:
            logger.error(f"[PublishFlowService] Release failed: {e}")

            # Publish failed; update the status and error info
            ext = self._get_latest_ext(publish_id)
            expected_ext = copy.deepcopy(ext)
            self._clear_retry_flag(ext)
            ext["error_message"] = str(e)
            # Roll back to ONLINE_PUB (the state the release runs within).
            ext["source_status"] = PublishStatus.ONLINE_PUB.value
            self._update_publish_status(
                publish_id=publish_id,
                target_status=PublishStatus.FAILED,
                source_status=PublishStatus.ONLINE_PUB,
                ext=ext,
                expected_ext=expected_ext,
            )

            return PublishFlowResult(
                publish_id=publish_id,
                status=PublishStatus.FAILED,
                message=f"Publish failed: {str(e)}",
                action="process",
            )
