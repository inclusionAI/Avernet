"""Eager teclaw container provisioning at bot creation.

When a **teclaw** bot is created, its container is provisioned **eagerly** via
BaaS (decision 2026-06-07): unlike ARCA/DaaS bots (which go through
``DeviceService.apply_device``), a teclaw bot composes its initial
``BotConfigArtifact`` and hands it to BaaS — BaaS forwards it to the external
container (non-mount). This mirrors the ``desktop`` bot precedent in
``bot_service.create_bot`` that skips ``apply_device`` for BaaS allocation.

Flow (``provision``):
1. produce the bot's initial deploy artifact via the teclaw
   ``DeployArtifactProducer`` (the same producer the publish build uses;
   ``ConfigComposer`` sits behind it, plus the engine_ext seam),
2. ``BaasService.create_teclaw_bot`` with the teclaw ``template_uuid`` + artifact
   → ``{bot_uuid, publish_id}``,
3. ``approve_publish`` to start the container (create + approve in one shot — a
   draft has no human approval gate),
4. record the device binding (``device_id = bot_uuid``, provider ``teclaw``,
   ``device_props.publish_id`` = the status read handle) so later
   ``resolve_container_provider`` finds the container via BaaS.

The binding is recorded ``PENDING`` and the baas ``publish_id`` is stashed in
``device_props``. The container boots asynchronously, so the status isn't known
yet — instead of reading through to baas on every status read, ``provision``
enqueues a durable publish-poll task. The task persists the resolved
``ACTIVE``/``FAILED`` onto the bot row + binding, making the stored column the
authoritative, eventually-consistent source of truth for list, detail, and
search reads (no per-read baas probe). That same task also pushes the bot's
passport-token outbound rule to the started container — the container's PaaS device
does not exist until BaaS executes the create publish, so it cannot be pushed
from ``provision`` (see the note there).

The container's *boot from artifact* and secbaas's ``start_device`` TECLAW arm
are the teclaw owner's side (in parallel); this wiring is built against the
contract and lights up end-to-end once the owner registers the teclaw template.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Iterable

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.bot_management.services.teclaw_publish_task_handler import (
    TECLAW_CREATE_PUBLISH_POLL_TASK,
    TECLAW_PUBLISH_TASK_DEADLINE_SECONDS,
    build_teclaw_publish_poll_payload,
)
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env


if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.bot import BotRepository
    from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
    from agentclaw.community.core.service_bot.services.baas_service import BaasService
    from agentclaw.community.core.service_bot.services.deploy.producer import (
        DeployArtifactProducerRouter,
    )
    from agentclaw.community.core.task_queue.services.task_queue_service import (
        TaskQueueService,
    )

logger = get_logger()

# Engine types whose bots run in a teclaw (pull-based external) container.
# Injectable so new external engines onboard without editing this module.
DEFAULT_TECLAW_ENGINE_TYPES = frozenset({"teclaw"})

_PROVISION_STAGE = "teclaw_provision"
_ROLLBACK_STAGE = "teclaw_provision_rollback"


@dataclass
class TeclawProvisionResult:
    """Outcome of an eager teclaw provision — mirrors apply_device's shape."""

    binding_id: int
    device_id: str
    status: str
    # The initial composed artifact delivered to the new container, so the caller
    # (create_bot) can record it onto the freshly-created draft publish row's ext.
    config_artifact: dict[str, Any] | None = None


class TeclawProvisionService:
    """Provisions a teclaw container at bot creation (create + approve)."""

    def __init__(
        self,
        *,
        baas_service: "BaasService",
        deploy_artifact_producer_router: "DeployArtifactProducerRouter",
        device_binding_repo: "DeviceBindingRepository",
        task_queue_service: "TaskQueueService",
        bot_repository: "BotRepository",
        teclaw_template_uuid: str,
        teclaw_engine_types: Iterable[str] = DEFAULT_TECLAW_ENGINE_TYPES,
    ) -> None:
        self._baas = baas_service
        self._producer_router = deploy_artifact_producer_router
        self._device_binding_repo = device_binding_repo
        self._task_queue_service = task_queue_service
        self._bot_repository = bot_repository
        self._teclaw_template_uuid = teclaw_template_uuid
        self._teclaw_engine_types = {e.strip().lower() for e in teclaw_engine_types}

    def is_teclaw(self, active_engine: str | None) -> bool:
        """Whether a bot with this engine runs in a teclaw container.

        This is the *creation-time intent* (what container to provision), not a
        container query — at creation BaaS does not know the bot yet. Reads of an
        existing bot's container go through ``resolve_container_provider``.
        """
        engine = (active_engine or "").strip().lower()
        return bool(engine) and engine in self._teclaw_engine_types

    def provision(
        self, *, bot: Dict[str, Any], owner_id: str
    ) -> TeclawProvisionResult:
        """Eagerly provision the teclaw container for a freshly created bot.

        Args:
            bot: Bot info dict (``bot_id`` / ``entity_id`` / ``entity_type`` /
                ``active_engine`` / ``bot_name`` / ``bot_desc``).
            owner_id: Creating user id (operator).

        Returns:
            :class:`TeclawProvisionResult` with the new binding id, the baas
            ``bot_uuid`` (stored as binding ``device_id``), and ``PENDING``
            status after durable poll enqueue succeeds. If enqueue fails and
            both failure-state writes succeed, returns ``FAILED``.

        Raises:
            BaasServiceError: provisioning failed (propagated to the caller, like
                ``apply_device`` failures, so bot creation surfaces the error).
            Exception: Persisting the bot or binding ``FAILED`` state after an
                enqueue failure failed; the persistence error is propagated.
        """
        bot_id = str(bot.get("bot_id", ""))
        entity_id = bot.get("entity_id", "")
        entity_type = bot.get("entity_type", "staff")
        env = get_current_env()
        request_id = self._request_id(entity_id, entity_type, bot_id, env)

        # 1. Produce the initial deploy artifact via the SAME teclaw producer the
        #    publish build uses, so eager provision and publish share one production
        #    path + engine_ext seam. ``version=None`` → draft/live, not a frozen
        #    publish snapshot. (``owner_id`` is spliced in for the producer's
        #    ``_compose_request``, which reads the bot row's ``owner_id``.) Runtime
        #    edits later push updates via the teclaw device-sync variant.
        deploy = self._producer_router.resolve(TECLAW_DEVICE_PROVIDER).produce_artifact(
            {**bot, "owner_id": owner_id}, version=None
        )
        config_artifact = deploy.ext["config_artifact"]

        # 2. Create the teclaw container in BaaS.
        result = self._baas.create_teclaw_bot(
            bot,
            owner_id=owner_id,
            request_id=request_id,
            config_artifact=config_artifact,
            template_uuid=self._teclaw_template_uuid,
        )
        bot_uuid = result.get("bot_uuid", "")
        publish_id = result.get("publish_id")
        from agentclaw.community.core.service_bot.services.baas_service import (
            BaasServiceError,
        )

        if not bot_uuid:
            # Create failed before BaaS minted a container — nothing to roll back.
            raise BaasServiceError(
                "create_teclaw_bot returned no bot_uuid for bot "
                f"{bot_id} (result={result})"
            )
        if publish_id is None:
            # A container was minted but BaaS handed back no publish workflow.
            # publish_id is the handle we need to approve (start) the container —
            # without it the create is unrecoverable. Roll back the orphan so we
            # don't leak a container we can never drive, then fail.
            self._best_effort_destroy(
                bot_uuid=bot_uuid,
                operator=owner_id,
                env=env,
                entity_id=entity_id,
                entity_type=entity_type,
                bot_id=bot_id,
            )
            raise BaasServiceError(
                "create_teclaw_bot returned no publish_id for bot "
                f"{bot_id} (bot_uuid={bot_uuid}); rolled back the orphaned "
                "container"
            )

        # 3. + 4. Approve (start) then record the binding. The BaaS bot now
        # exists; if approve or insert fails, best-effort destroy it so we don't
        # leak an orphaned container (the caller soft-deletes the local record).
        try:
            # Draft has no human approval gate — approve immediately to start.
            self._baas.approve_publish(
                publish_id=publish_id, operator=owner_id, request_id=request_id
            )
            binding_id = self._device_binding_repo.insert_binding(
                entity_id=entity_id,
                entity_type=entity_type,
                device_id=bot_uuid,
                device_provider=TECLAW_DEVICE_PROVIDER,
                env=env,
                # publish_id 用于状态回填；其余 id 供后续从 binding 热更新
                # BaaS outbound rule，避免再依赖 Teclaw 专属查询。
                device_props={
                    "publish_id": publish_id,
                    "bot_uuid": bot_uuid,
                    "bolt_id": bot_id,
                    "entity_id": owner_id,
                },
                status=DeviceBindingStatus.PENDING,
                apply_reason=f"Create teclaw bot: {bot.get('bot_name') or bot_id}",
                applied_by=owner_id,
            )
        except Exception:
            self._best_effort_destroy(
                bot_uuid=bot_uuid,
                operator=owner_id,
                env=env,
                entity_id=entity_id,
                entity_type=entity_type,
                bot_id=bot_id,
            )
            raise

        try:
            self._task_queue_service.enqueue(
                TECLAW_CREATE_PUBLISH_POLL_TASK,
                build_teclaw_publish_poll_payload(
                    binding_id=binding_id,
                    bot_id=bot_id,
                    owner_id=owner_id,
                    publish_id=int(publish_id),
                    started_at_epoch_s=time.time(),
                ),
                deadline_seconds=TECLAW_PUBLISH_TASK_DEADLINE_SECONDS,
            )
        except Exception as exc:
            logger.exception(
                "[TeclawProvisionService.provision] enqueue publish poll failed: "
                "bot_id=%s binding_id=%s publish_id=%s error=%s",
                bot_id,
                binding_id,
                publish_id,
                exc,
            )
            self._mark_enqueue_failed(
                bot_id=bot_id,
                owner_id=owner_id,
                binding_id=binding_id,
            )
            return TeclawProvisionResult(
                binding_id=binding_id,
                device_id=bot_uuid,
                status=DeviceBindingStatus.FAILED.value,
                config_artifact=config_artifact,
            )

        # The passport-token outbound rule is NOT pushed here: BaaS only mints the
        # container's PaaS device (``provider_device_id``) while it executes the
        # create publish, and under all-auto approval (#197) both ``create`` and
        # the client ``approve`` return before that. A push here would query BaaS
        # for devices that have no ``provider_device_id`` yet and silently write
        # nothing. It rides on the create publish poll instead —
        # ``TeclawPublishTaskHandler`` pushes it once the publish is observed
        # SUCCESS, mirroring the publish path's poll-success refresh.
        logger.info(
            "[TeclawProvisionService.provision] Provisioned teclaw container: "
            "bot_id=%s, bot_uuid=%s, publish_id=%s, binding_id=%s",
            bot_id,
            bot_uuid,
            publish_id,
            binding_id,
        )

        return TeclawProvisionResult(
            binding_id=binding_id,
            device_id=bot_uuid,
            status=DeviceBindingStatus.PENDING,
            config_artifact=config_artifact,
        )

    def _mark_enqueue_failed(
        self,
        *,
        bot_id: str,
        owner_id: str,
        binding_id: int,
    ) -> None:
        updated = self._bot_repository.update_by_owner(
            bot_id,
            owner_id,
            {"status": DeviceBindingStatus.FAILED.value},
        )
        if updated is None:
            raise RuntimeError("bot status update matched no record")
        self._device_binding_repo.update_status(
            binding_id=binding_id,
            status=DeviceBindingStatus.FAILED.value,
        )

    def _best_effort_destroy(
        self,
        *,
        bot_uuid: str,
        operator: str,
        env: str,
        entity_id: str,
        entity_type: str,
        bot_id: str,
    ) -> None:
        """Compensating destroy of a BaaS bot that was created but failed to
        approve/bind — best-effort, never raises (the original error wins)."""
        try:
            self._baas.destroy_bot(
                bot_uuid=bot_uuid,
                operator=operator,
                request_id=self._request_id(
                    entity_id, entity_type, bot_id, env, stage=_ROLLBACK_STAGE
                ),
            )
            logger.info(
                "[TeclawProvisionService] compensating destroy of orphaned "
                "teclaw bot_uuid=%s",
                bot_uuid,
            )
        except Exception as e:
            logger.error(
                "[TeclawProvisionService] compensating destroy failed for "
                "bot_uuid=%s: %s (BaaS TTL will reclaim it)",
                bot_uuid,
                e,
            )

    @staticmethod
    def _request_id(
        entity_id: str,
        entity_type: str,
        bot_id: str,
        env: str,
        stage: str = _PROVISION_STAGE,
    ) -> str:
        """Deterministic 32-char request id (mirrors build_service's scheme) so
        retries are idempotent on the BaaS side. ``stage`` distinguishes the
        provision request from the compensating-destroy request."""
        raw = f"{entity_id}_{entity_type}_{bot_id}_{env}_{stage}"
        return hashlib.md5(raw.encode()).hexdigest()
