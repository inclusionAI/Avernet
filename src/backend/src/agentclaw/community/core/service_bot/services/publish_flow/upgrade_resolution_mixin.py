"""First-release vs. upgrade resolution, mixed into ``PublishFlowService``.

Both release stages must decide whether to create a new BaaS bot (first release)
or reuse an existing one (upgrade). That decision — reading the current/previous
publish record's binding and the previous release's status — is a self-contained,
read-only concern kept here rather than inline in the release orchestration:

* :meth:`resolve_verify_binding` — verify stage: find an existing verify bot to
  upgrade (current record's ``ext.binding.verify``, else the previous record's).
* :meth:`should_upgrade_online` — online stage: whether the previous publish is
  a released bot that this publish should upgrade in place.
"""
from __future__ import annotations

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

logger = get_logger()

# BaaS bot statuses under which the previous online bot is NOT a valid in-place
# UPGRADE (UPDATE) target — the bot is gone or not live, so re-publish must take
# the first-release (CREATE) path instead.
#
# The critical case is ``STOPPED``: offlining a SUCCESS publish tears down the
# online bot via a BaaS STOP, which for a TeClaw device physically destroys the
# underlying bot and leaves ``baas_bot.status = STOPPED`` (not ``RELEASED``).
# Treating only ``RELEASED`` as "gone" let a ``STOPPED`` previous bot slip
# through as an UPGRADE target, so the re-publish issued an UPDATE against the
# destroyed device and failed permanently with ``DEVICE_NOT_FOUND``.
_ONLINE_UPGRADE_BLOCKING_BAAS_STATUSES = frozenset(
    {"RELEASED", "STOPPED", "STOPPING", "FAILED"}
)


class UpgradeResolutionMixin:
    """Decide first-release vs. upgrade for the verify and online stages."""

    def _resolve_verify_binding(
        self,
        publish_record: BotPublishRecord,
        ext: dict,
    ) -> tuple[int | None, str | None]:
        """Resolve the verify environment's binding info and determine whether to take the upgrade path.

        Looks up the verify environment's existing Bot binding by priority:
        1. The current publish record's ext.binding.verify
        2. The previous publish record's (last_pub_id) ext.binding.verify

        Args:
            publish_record: Current publish record
            ext: The current publish record's ext field

        Returns:
            tuple[int | None, str | None]: (verify_binding_id, bot_uuid)
                - If a valid binding is found, returns (binding_id, bot_uuid)
                - If not found, returns (None, None), indicating a first release is needed
        """
        publish_id = publish_record.id

        # Priority 1: the current publish record's ext.binding.verify
        current_verify_binding_id = ext.get("binding", {}).get(PublishStage.VERIFY.value)
        if current_verify_binding_id:
            binding = self._publish_service.get_device_binding_by_id(current_verify_binding_id)
            if binding and binding.device_id:
                logger.info(
                    f"[PublishFlowService._resolve_verify_binding] "
                    f"Verify Bot exists in current record: publish_id={publish_id}, "
                    f"bot_uuid={binding.device_id}, binding_id={current_verify_binding_id}"
                )
                return current_verify_binding_id, binding.device_id

        # Priority 2: fetch from the previous publish record's ext.binding.verify (upgrade release scenario)
        if not publish_record.last_pub_id or publish_record.last_pub_id <= 0:
            logger.info(
                f"[PublishFlowService._resolve_verify_binding] "
                f"No verify binding found, will do first release: publish_id={publish_id}"
            )
            return None, None

        last_publish = self._publish_service.get_publish_by_id(publish_record.last_pub_id)
        if not last_publish:
            logger.info(
                f"[PublishFlowService._resolve_verify_binding] "
                f"No verify binding found, will do first release: publish_id={publish_id}"
            )
            return None, None

        last_ext = last_publish.ext or {}
        last_verify_binding_id = last_ext.get("binding", {}).get(PublishStage.VERIFY.value)
        if not last_verify_binding_id:
            logger.info(
                f"[PublishFlowService._resolve_verify_binding] "
                f"No verify binding found, will do first release: publish_id={publish_id}"
            )
            return None, None

        binding = self._publish_service.get_device_binding_by_id(last_verify_binding_id)
        if not binding or not binding.device_id:
            logger.info(
                f"[PublishFlowService._resolve_verify_binding] "
                f"No verify binding found, will do first release: publish_id={publish_id}"
            )
            return None, None

        logger.info(
            f"[PublishFlowService._resolve_verify_binding] "
            f"Verify Bot exists in last record (last_pub_id={publish_record.last_pub_id}): "
            f"publish_id={publish_id}, bot_uuid={binding.device_id}, "
            f"binding_id={last_verify_binding_id}"
        )
        return last_verify_binding_id, binding.device_id

    def _should_upgrade_online(self, publish_record: BotPublishRecord) -> bool:
        """Determine whether the online publish stage should take the upgrade release path.

        The upgrade scenario requires all of the following:
        1. The current publish record has a valid last_pub_id
        2. The previous publish record exists
        3. The previous publish record's status is released (i.e. PublishStatus.SUCCESS)
        4. The previous online bot is still live in BaaS — its ``baas_bot_status``
           is not one of the gone/not-live statuses in
           ``_ONLINE_UPGRADE_BLOCKING_BAAS_STATUSES`` (notably ``STOPPED``, which
           is what an offline teardown leaves behind on a destroyed TeClaw bot).

        Otherwise, uniformly treat it as a first release and create a new online Bot.
        """
        last_pub_id = publish_record.last_pub_id
        if not last_pub_id or last_pub_id <= 0:
            return False

        last_publish = self._publish_service.get_publish_by_id(last_pub_id)
        if not last_publish:
            logger.warning(
                f"[PublishFlowService._should_upgrade_online] "
                f"Last publish record not found, fallback to first release: last_pub_id={last_pub_id}"
            )
            return False

        try:
            last_status = PublishStatus(last_publish.status)
        except ValueError:
            logger.warning(
                f"[PublishFlowService._should_upgrade_online] "
                f"Invalid last publish status, fallback to first release: "
                f"last_pub_id={last_pub_id}, status={last_publish.status}"
            )
            return False

        if last_status != PublishStatus.SUCCESS and last_status != PublishStatus.RELEASED:
            logger.info(
                f"[PublishFlowService._should_upgrade_online] "
                f"Last publish is not released, fallback to first release: "
                f"last_pub_id={last_pub_id}, status={last_status}"
            )
            return False

        baas_status_result = self.get_publish_bot_status(last_pub_id, PublishStage.ONLINE)
        baas_status = baas_status_result.get("baas_bot_status")
        if baas_status in _ONLINE_UPGRADE_BLOCKING_BAAS_STATUSES:
            logger.info(
                f"[PublishFlowService._should_upgrade_online] "
                f"Previous online bot is gone/not live, fallback to first release: "
                f"last_pub_id={last_pub_id}, baas_bot_status={baas_status}"
            )
            return False

        return True
