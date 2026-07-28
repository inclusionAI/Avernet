"""First-release vs. upgrade resolution, mixed into ``PublishFlowService``.

Both release stages must decide whether to create a new BaaS bot (first release)
or reuse an existing one (upgrade). That decision — reading the current/previous
publish record's binding and the previous release's status — is a self-contained,
read-only concern kept here rather than inline in the release orchestration:

* :meth:`_resolve_verify_binding` — verify stage: find an existing verify bot to
  upgrade (current record's ``ext.binding.verify``, else the previous record's).
* :meth:`_resolve_online_reuse_target` — online stage: the candidate bot to reuse
  (this record's own ``ext.binding.online`` first, else the previous record's).
* :meth:`_decide_online_deploy` — online stage: the provider-aware upgrade /
  retire-then-first-release / first-release decision from the candidate's live
  BaaS status.
"""
from __future__ import annotations

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
)
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)
from agentclaw.community.core.service_bot.services.publish_flow.errors import (
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.types import (
    OnlineDeployDecision,
    PublishStage,
)
from agentclaw.community.log import get_logger

logger = get_logger()

# BaaS bot statuses that mean the candidate is gone / self-terminating, so there
# is nothing live to reuse or orphan — go straight to a fresh first release.
_ONLINE_GONE_BAAS_STATUSES = frozenset({"RELEASED", "DESTROYING"})

# BaaS bot statuses where the record still exists but the container is not live.
# Whether these can be reused depends on the provider: a ``baas``/ARCA UPDATE
# destroys+recreates the device in place (recovers), while a teclaw UPDATE only
# re-delivers to the existing container and cannot rebuild a gone one — so for
# teclaw these must be retired and recreated instead of upgraded.
_ONLINE_NOT_LIVE_BAAS_STATUSES = frozenset({"FAILED", "STOPPED", "STOPPING"})


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

    def _resolve_online_reuse_target(
        self, publish_record: BotPublishRecord
    ) -> tuple[str | None, int | None]:
        """Resolve the candidate online bot this deploy could reuse, as
        ``(bot_uuid, binding_id)``.

        Priority mirrors the verify resolver, but crucially checks **this record's
        own** online binding first — so a retry of a *failed first release* treats
        the failed attempt's bot as the reuse candidate (previously invisible,
        which is what orphaned it):

        1. this record's ``ext.binding.online`` → binding → ``device_id``
        2. else the previous record's (``last_pub_id``) ``ext.binding.online``
        3. else ``(None, None)`` — nothing to reuse, first release.
        """
        ext = publish_record.ext or {}
        own_binding_id = ext.get("binding", {}).get(PublishStage.ONLINE.value)
        if own_binding_id:
            binding = self._publish_service.get_device_binding_by_id(own_binding_id)
            if binding and binding.device_id:
                return binding.device_id, own_binding_id

        last_pub_id = publish_record.last_pub_id
        if not last_pub_id or last_pub_id <= 0:
            return None, None

        last_publish = self._publish_service.get_publish_by_id(last_pub_id)
        if not last_publish:
            return None, None

        last_binding_id = (last_publish.ext or {}).get("binding", {}).get(
            PublishStage.ONLINE.value
        )
        if not last_binding_id:
            return None, None

        binding = self._publish_service.get_device_binding_by_id(last_binding_id)
        if not binding or not binding.device_id:
            return None, None
        return binding.device_id, last_binding_id

    def _decide_online_deploy(
        self, publish_record: BotPublishRecord, bot: dict
    ) -> OnlineDeployDecision:
        """Decide how the online deploy should treat the reuse candidate.

        Single, provider-aware rule shared by every online deploy seam. Reads the
        candidate's live BaaS status and the bot's container provider:

        - no candidate / ``RELEASED`` / ``DESTROYING`` → ``FIRST_RELEASE``
          (nothing live to reuse or orphan).
        - ``ACTIVE`` → ``UPGRADE`` (re-deliver in place; works for both providers).
        - ``FAILED`` / ``STOPPED`` / ``STOPPING`` → ``UPGRADE`` for ``baas``/ARCA
          (the UPDATE destroys+recreates the device in place and recovers it), but
          ``RETIRE_THEN_FIRST_RELEASE`` for teclaw (its UPDATE cannot rebuild a
          gone container — it would just fail the publish and strand the record).
        - anything else (``PENDING``/unknown) → ``UPGRADE`` (optimistic; the deploy
          atom / progress poll settles a still-provisioning bot).

        ``get_bot`` already normalizes a genuine 404 to ``{"status":"RELEASED"}``,
        so a raised error here means a transient/non-404 BaaS failure — NOT that
        the candidate is gone. We let it propagate so the durable task retries the
        status read rather than creating a replacement for a possibly-live bot.
        An empty/absent status on a *successful* envelope is likewise ambiguous
        (``get_bot`` returns the response ``data`` which defaults to ``{}``), so
        we refuse to treat it as gone and raise so the task retries — only an
        explicit terminal status recreates.
        """
        bot_uuid, _ = self._resolve_online_reuse_target(publish_record)
        if not bot_uuid:
            return OnlineDeployDecision.FIRST_RELEASE

        # No try/except: a raised error is a transient/non-404 failure (a real 404
        # is already normalized to RELEASED); propagate so the deploy retries.
        baas_bot = self._baas_service.get_bot(bot_uuid=bot_uuid)
        status = (baas_bot or {}).get("status")

        if not status:
            # A genuine 404 is already normalized to RELEASED; an empty/absent
            # status from a 200 envelope is ambiguous — NOT proof the candidate is
            # gone. Refuse to recreate on it; raise so the durable task retries the
            # status read rather than replacing a possibly-live bot.
            raise PublishFlowServiceError(
                f"BaaS get_bot returned no status for candidate "
                f"bot_uuid={bot_uuid}; refusing to treat as gone"
            )

        if status in _ONLINE_GONE_BAAS_STATUSES:
            return OnlineDeployDecision.FIRST_RELEASE

        if status in _ONLINE_NOT_LIVE_BAAS_STATUSES:
            is_teclaw = (
                self._baas_service.resolve_container_provider(bot)
                == TECLAW_DEVICE_PROVIDER
            )
            decision = (
                OnlineDeployDecision.RETIRE_THEN_FIRST_RELEASE
                if is_teclaw
                else OnlineDeployDecision.UPGRADE
            )
            logger.info(
                f"[PublishFlowService._decide_online_deploy] "
                f"candidate not live: bot_uuid={bot_uuid}, status={status}, "
                f"is_teclaw={is_teclaw} -> {decision.value}"
            )
            return decision

        # ACTIVE / PENDING / unknown → reuse in place.
        return OnlineDeployDecision.UPGRADE
