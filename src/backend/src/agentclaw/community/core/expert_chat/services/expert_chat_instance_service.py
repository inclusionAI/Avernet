"""ExpertChatInstanceService — per-caller baas container lifecycle.

Owns the ``ac_expert_chat_instance`` ledger and the four-step
provisioning/reuse/revive dance for a caller's independent container
(see ``specs/2026-07-13-caller-instance/design.md`` §4):

1. look up / create the instance row keyed by ``(bot_id, owner_id,
   user_id, env)``;
2. reverse-look the owner service-bot success publish order to get the
   build artifact (``migration_path``);
3. first time (no ``bot_uuid`` yet): ``create_bot``
   (``auto_approve_publish=True``) returns the baas publish order;
4. subsequent: ``get_bot`` — active reuses; ``RELEASED`` is re-upgraded via
   ``upgrade_bot`` (``bot_uuid`` unchanged), falling back to
   ``create_bot`` (new ``bot_uuid``) on ``BOT_NOT_FOUND``;

   After Step 3/4, the kicked-off publish workflow (if any) is polled once
   via ``get_publish_progress`` to ``SUCCESS``; the full progress trail is
   persisted to the instance ext under ``baas_publish`` (each snapshot
   carries the BaaS workflow ``publish_id``), and ``status`` flipped to
   ``active``. Reuse (already active, no new workflow) skips the poll.

The returned ``connection`` mirrors ``ExpertChatService.get_chat_session``'s
``connection`` shape; ``session_key`` stays with ``ExpertChatService`` (D3).
``ExpertChatService`` is intentionally NOT modified here — this service
stands alone and is wired into the DI graph for callers to inject.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.expert_chat.errors import (
    BotNotPublishedError,
    ConnectionError,
)
from agentclaw.community.core.expert_chat.repository import ExpertChatInstanceRepository
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BaasServiceError,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


class ExpertChatInstanceService:
    """Provision + reuse a per-caller baas container for a service bot.

    Dependencies are injected (see ``di/modules/expert_chat_module.py``):
    ``ExpertChatInstanceRepository`` (ledger), ``BaasService`` (container
    lifecycle), ``BotPublishRepositoryProtocol`` (success publish order /
    migration_path reverse-lookup), ``BotRepository`` (bot info lookup).
    """

    @inject
    def __init__(
        self,
        instance_repo: ExpertChatInstanceRepository,
        baas_service: BaasService,
        bot_publish_repo: BotPublishRepositoryProtocol,
        bot_repo: BotRepository,
    ) -> None:
        self._instance_repo = instance_repo
        self._baas = baas_service
        self._publish_repo = bot_publish_repo
        self._bot_repo = bot_repo

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------
    async def get_caller_connection(
        self,
        user_id: str,
        bot_id: str,
        owner_id: str,
    ) -> Dict[str, Any]:
        """Return the caller's container ``connection``.

        Mirrors ``ExpertChatService.get_chat_session``'s ``connection``
        shape; ``session_key`` is NOT produced here (D3 — that stays with
        ``ExpertChatService``).

        Raises:
            BotNotPublishedError: no success publish order for the service
                bot — nothing to reproduce the container from.
            ConnectionError: baas lifecycle / device resolution failure.
            BaasServiceError: baas write failures propagate (D5 — never
                silently swallowed).
        """

        publish_record, migration_path = self._resolve_build_artifact(
            bot_id, owner_id
        )
        version = publish_record.version or 1

        # --- Step 1: look up / create instance row ---
        instance = self._instance_repo.get_instance(user_id, bot_id, owner_id)
        if instance is None:
            instance = self._instance_repo.upsert_instance(
                user_id=user_id,
                bot_id=bot_id,
                owner_id=owner_id,
                status="init",
                ext=None,
            )

        # If instance is already success, check if version upgrade is needed
        if instance.get("status") == "success":
            ext = instance.get("ext") or {}
            bot_uuid = ext.get("bot_uuid")
            instance_version = ext.get("version") or 0
            if bot_uuid and version <= instance_version:
                connection = self._build_connection(
                    bot_uuid=bot_uuid,
                    bot_id=bot_id,
                    user_id=user_id,
                )
                logger.info(
                    "[ExpertChatInstance] Instance already success: bot=%s owner=%s user=%s bot_uuid=%s",
                    bot_id, owner_id, user_id, bot_uuid,
                )
                return {
                    "instance": instance,
                    "connection": connection,
                    "need_poll": False,
                }

        ext = instance.get("ext") or {}
        bot_uuid = ext.get("bot_uuid")

        service_bot_publish_id = publish_record.id

        # ``publish_id`` of the baas workflow just kicked off (None when no
        # container was provisioned/upgraded this round, i.e. reuse). The
        # progress poll runs once, here in the caller, after create/revive.
        baas_publish_id: Optional[Any] = None

        if not bot_uuid:
            # --- Step 3: never provisioned — raise a fresh container ---
            order = self._create_container(
                bot_id=bot_id,
                owner_id=owner_id,
                user_id=user_id,
                migration_path=migration_path,
                version=version,
            )
            bot_uuid = order["bot_uuid"]
            baas_publish_id = order.get("publish_id")
            ext = {
                "bot_uuid": bot_uuid,
                "service_bot_publish_id": service_bot_publish_id,
                "version": version,
                "baas_publish_id": baas_publish_id,
            }
        else:
            # --- Step 4: provisioned before — upgrade container ---
            # container recycled (RELEASED): upgrade with bot_uuid preserved;
            # BOT_NOT_FOUND → fall back to a fresh create_bot.
            upgraded = self._upgrade_container(
                bot_uuid=bot_uuid,
                bot_id=bot_id,
                owner_id=owner_id,
                user_id=user_id,
                migration_path=migration_path,
                version=version,
            )
            bot_uuid = upgraded["bot_uuid"]
            baas_publish_id = upgraded.get("publish_id")
            ext = dict(ext)
            ext["baas_publish_id"] = baas_publish_id
            if "bot_uuid" not in ext:
                ext["bot_uuid"] = bot_uuid

        # Update instance with new ext
        self._instance_repo.update_instance(
            user_id=user_id,
            bot_id=bot_id,
            owner_id=owner_id,
            ext=ext,
        )

        if baas_publish_id:
            progress = self._baas.get_publish_progress(
                publish_id=int(baas_publish_id),
                include_devices=True,
            )
            status = progress.get("status")
            instance_status = "success" if status == "SUCCESS" else "failed" if status == "FAILED" else instance.get("status")
            ext = dict(ext)
            ext["baas_publish"] = progress
            self._instance_repo.update_instance(
                user_id=user_id,
                bot_id=bot_id,
                owner_id=owner_id,
                status=instance_status,
                ext=ext,
            )

        instance = self._instance_repo.get_instance(user_id, bot_id, owner_id)
        if instance.get("status") != "success":
            logger.info(
                "[ExpertChatInstance] Instance not ready, need poll: bot=%s owner=%s user=%s status=%s",
                bot_id, owner_id, user_id, instance.get("status"),
            )

            return {
                "instance": instance,
                "connection": None,
                "need_poll": True,
            }

        connection = self._build_connection(
            bot_uuid=bot_uuid,
            bot_id=bot_id,
            user_id=user_id,
        )

        logger.info(
            "[ExpertChatInstance] Caller connection ready: bot=%s owner=%s "
            "user=%s bot_uuid=%s",
            bot_id, owner_id, user_id, bot_uuid,
        )

        return {
            "instance": instance,
            "connection": connection,
            "need_poll": False,
        }

    # ------------------------------------------------------------------
    # Step 2: build-artifact reverse lookup
    # ------------------------------------------------------------------
    def _resolve_build_artifact(
        self,
        bot_id: str,
        owner_id: str,
    ) -> tuple[Any, Optional[str]]:
        """Resolve the owner success publish order + migration_path.

        Same source as ``BaasService.get_bind_id(SUCCESS)``: the latest
        success publish order under ``(publish_bot_id=bot_id, owner_id,
        env)``. ``migration_path`` rides on that publish order's ``ext``
        (written by build, read by verify/online/release — see
        ``publish_flow_service.py``); the instance never holds it.

        No source-bot back-lookup: the publish order + ``bot_id`` carry
        everything the baas payload needs (``bot_name``/``owner_id``/
        ``version`` from the record; the rest is baas-side resolved).
        """
        env = get_current_env()
        publish_record = self._publish_repo.get_by_publish_bot_id(
            publish_bot_id=bot_id,
            owner_id=owner_id,
            env=env,
            publish_status=PublishStatus.SUCCESS.value,
        )
        if publish_record is None:
            logger.warning(
                "[ExpertChatInstance] No success publish order: bot=%s owner=%s",
                bot_id, owner_id,
            )
            raise BotNotPublishedError(
                f"Service bot has no success publish order: {bot_id} (owner={owner_id})"
            )

        migration_path = (publish_record.ext or {}).get("migration_path")
        return publish_record, migration_path

    # ------------------------------------------------------------------
    # Step 3 + fallback: create a fresh container
    # ------------------------------------------------------------------
    def _create_container(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        migration_path: Optional[str],
        version: int = 1,
    ) -> Dict[str, Any]:
        """Call ``create_bot`` (auto-approve) and return the publish order.

        ``auto_approve_publish=True`` so the create workflow is self-
        approved (no explicit ``approve_publish`` call). Returns the baas
        publish order — ``{bot_uuid, publish_id}`` — without querying the
        workflow; the caller queries ``get_publish_progress`` once and
        persists the trail (``baas_publish``) to the instance ext.

        Raises:
            ConnectionError: create_bot failed or returned no bot_uuid (D5).
        """
        bot_info = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot_info:
            raise ConnectionError(
                f"Bot not found: bot_id={bot_id} owner_id={owner_id}",
                error_code="5001",
            )
        request_id = self._request_id(bot_info, user_id, "caller_create")
        try:
            result = self._baas.create_bot(
                bot=bot_info,
                owner_id=user_id,
                request_id=request_id,
                migration_path=migration_path,
                stage=PublishStage.ONLINE.value,
                version=str(version),
                auto_approve_publish=True,
            )
        except BaasServiceError:
            raise
        except Exception as e:
            logger.error(
                "[ExpertChatInstance] create_bot failed: bot=%s user=%s: %s",
                bot_id, user_id, e,
            )
            raise ConnectionError(
                f"Failed to create caller container: {e}",
                error_code="5001",
                original_error=str(e),
            )

        bot_uuid = result.get("bot_uuid")
        publish_id = result.get("publish_id")
        if not bot_uuid:
            raise ConnectionError(
                "create_bot returned no bot_uuid",
                error_code="5001",
                original_error=str(result),
            )

        return {"bot_uuid": bot_uuid, "publish_id": publish_id}

    # ------------------------------------------------------------------
    # Step 4.2: upgrade a recycled container
    # ------------------------------------------------------------------
    def _upgrade_container(
        self,
        bot_uuid: str,
        bot_id: str,
        owner_id: str,
        user_id: str,
        migration_path: Optional[str],
        version: int = 1,
    ) -> Dict[str, Any]:
        """Upgrade a RELEASED container, preferring ``bot_uuid`` preservation.

        ``upgrade_bot`` keeps ``bot_uuid``; on ``BOT_NOT_FOUND`` (container
        fully aged out of baas) it falls back to ``create_bot`` and returns
        a new ``bot_uuid``. Returns the baas publish order —
        ``{bot_uuid, publish_id}`` — without querying the workflow; the
        caller queries ``get_publish_progress`` once and persists the
        trail (``baas_publish``) to the instance ext. Both the in-place
        upgrade and the fallback-create path surface a ``publish_id`` so
        the caller queries uniformly.
        """
        bot_info = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot_info:
            raise ConnectionError(
                f"Bot not found: bot_id={bot_id} owner_id={owner_id}",
                error_code="5001",
            )
        request_id = self._request_id(bot_info, user_id, "caller_upgrade")
        try:
            result = self._baas.upgrade_bot(
                bot_uuid=bot_uuid,
                bot=bot_info,
                owner_id=user_id,
                request_id=request_id,
                migration_path=migration_path,
                stage=PublishStage.ONLINE.value,
                version=str(version),
            )
            logger.info(
                "[ExpertChatInstance] upgrade upgraded in place: bot_uuid=%s",
                bot_uuid,
            )
            return {
                "bot_uuid": bot_uuid,
                "publish_id": result.get("publish_id"),
            }
        except Exception as e:
            logger.error(
                "[ExpertChatInstance] upgrade_bot failed: bot_uuid=%s: %s",
                bot_uuid, e,
            )
            raise ConnectionError(
                f"Failed to upgrade caller container: {e}",
                error_code="5001",
                original_error=str(e),
            )

    # ------------------------------------------------------------------
    # Connection production
    # ------------------------------------------------------------------
    def _build_connection(
        self,
        bot_uuid: str,
        bot_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Produce a connection via BaaS ws-info API.

        Reuses ``BaasService.get_ws_info_by_bot_uuid`` — direct bot_uuid lookup
        without needing a local binding record.
        """
        try:
            ws_info = self._baas.get_ws_info_by_bot_uuid(
                bot_uuid=bot_uuid,
                device_affinity=user_id,
            )
        except BaasServiceError as e:
            logger.error(
                "[ExpertChatInstance] get_ws_info_by_bot_uuid failed: bot=%s "
                "bot_uuid=%s: %s",
                bot_id, bot_uuid, e,
            )
            raise ConnectionError(
                f"Failed to get ws info: {e}",
                error_code="5001",
                original_error=str(e),
            ) from e
        except Exception as e:
            logger.error(
                "[ExpertChatInstance] get_ws_info_by_bot_uuid failed: bot=%s "
                "bot_uuid=%s: %s",
                bot_id, bot_uuid, e,
            )
            raise ConnectionError(
                "无法连接到Bot服务",
                error_code="5001",
                original_error=str(e),
            ) from e

        return {
            "ws_url": ws_info.ws_url,
            "token": ws_info.token,
            "target": ws_info.target,
            "paas_device_id": ws_info.paas_device_id,
            "baas_base_url": ws_info.baas_base_url,
            "engine_port": ws_info.engine_port,
            "tenant": ws_info.tenant,
            "bot_uuid": ws_info.bot_uuid,
        }

    @staticmethod
    def _request_id(bot: Dict[str, Any], user_id: str, stage: str) -> str:
        """Best-effort request id (idempotency) for baas calls.

        Baas wants 32–64 char [A-Za-z0-9_-] ids. We don't have the full
        ``BotBuildService.generate_request_id`` hash here (would couple
        this service to a sibling it doesn't otherwise need); a stable
        md5 of the same-ish inputs is good enough and stays deterministic
        per (caller, bot, env, stage) — ``user_id`` keeps each caller's
        baas workflow id distinct so a second caller's create/upgrade is
        not deduped against the first's.
        """
        import hashlib

        entity_id = bot.get("entity_id", "")
        bot_id = bot.get("bot_id", "")
        env = get_current_env()
        raw = f"{entity_id}_{bot_id}_{user_id}_{env}_{stage}"
        request_id = hashlib.md5(raw.encode()).hexdigest()
        logger.info(
            "[ExpertChatInstance] _request_id: entity_id=%s bot_id=%s user=%s "
            "stage=%s request_id=%s",
            entity_id, bot_id, user_id, stage, request_id,
        )
        return request_id