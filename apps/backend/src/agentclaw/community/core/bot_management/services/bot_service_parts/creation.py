"""
``create_bot`` with creation-template preparation, ``provision_bot`` and the
creation-confirmation retry — moved verbatim from ``bot_service.py``.

Part of the mechanical split of ``bot_service.py``: ``BotCreationMixin`` members were
moved here verbatim and the facade composes them onto ``BotService``.
"""

from typing import Any, Dict, Optional

from agentclaw.community.core.bot_management.bcn_identity import provider_address_kwargs
from agentclaw.community.core.bot_management.bot_quota import BotQuotaError
from agentclaw.community.core.bot_management.collaboration_repair.profile import (
    outbound_avatar_from_record,
)
from agentclaw.community.core.devices.errors import DeviceServiceError
from agentclaw.community.core.service_bot.services.arca_image_pin import (
    apply_default_image_to_ext,
    clear_image_policy_from_ext,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE, _get_engine_types
from agentclaw.community.utils.env_utils import get_current_env

from .common import (
    BotNameExistsError,
    BotServiceError,
    PROVISIONING_CLAIM_STALE_SECONDS,
    generate_bot_id,
    logger,
    validate_bot_name,
)


class BotCreationMixin:
    """``create_bot`` with creation-template preparation, ``provision_bot`` and the
    creation-confirmation retry."""

    def create_bot(
        self,
        user_id: str,
        nick_name: str,
        bot_name: Optional[str] = None,
        bot_desc: Optional[str] = None,
        entity_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        share_policy: Optional[Dict[str, Any]] = None,
        engine_type: Optional[str] = None,
        ext: Optional[Dict[str, Any]] = None,
        bot_id: Optional[str] = None,
        bot_type: Optional[str] = None,
        template_type: Optional[str] = None,
        template_config: Optional[Dict[str, Any]] = None,
        cookie: Optional[str] = None,
        space_id: Optional[int] = None,
        provision: bool = True,
        space_quota: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a new bot with async device allocation.

        Args:
            user_id: User ID (used as creator and for default entity)
            nick_name: User nickname for device allocation and first bot naming
            bot_name: Bot name (optional, defaults based on rules)
            bot_desc: Bot description (optional)
            entity_id: Entity ID (defaults to staff_{user_id})
            entity_type: Entity type (defaults to 'staff')
            share_policy: Sharing configuration (optional)
            engine_type: Active engine type (default: uses config default)
            cookie: User's session cookie for memoryos API authentication (optional)
            ext: Extension field dictionary (optional), e.g., {"avatar_url": "https://..."}
            bot_id: Bot ID (optional, used for Passport authorization continuation)
            bot_type: Bot type (default: "personal", can be "personal" or "service")
            template_type: Template type (optional, e.g., "applicationCoding")
            template_config: Template configuration dictionary (optional)
            provision: ``False`` writes the record (steps 1 and 1.5) and
                returns it ``PENDING`` with no binding; :meth:`provision_bot`
                runs the rest later (W8, teclaw platform-managed creation).

        Returns:
            Created bot record with PENDING status and binding info

        Raises:
            BotServiceError: If bot creation fails

        Note:
            Naming rules:
            - First bot (user has no bots): uses nick_name (花名) as default
            - Non-first bot: uses bot_id as default if bot_name not provided
        """
        # ===== 入参校验：bot_name 合法性（空/字符/长度） =====
        # 兼容老调用：bot_name 允许 None — None 时跳过显式校验，由 _resolve_bot_name
        # 走默认命名规则；只有传了非 None 才严格校验。
        if bot_name is not None:
            bot_name = validate_bot_name(bot_name)

        # ===== 幂等性检查：如果传入 bot_id，检查是否已存在 =====
        if bot_id is not None:
            existing_bot = self._repository.get_by_id_and_owner(bot_id, user_id)
            if existing_bot:
                existing_bot_type = str(existing_bot.get("bot_type") or "personal")
                existing_template_type = template_type or existing_bot.get(
                    "template_type"
                )
                existing_status = str(existing_bot.get("status") or "")
                if existing_bot.get("binding_id") or existing_status not in {
                    "PENDING",
                    "PROVISIONING",
                }:
                    if provision and existing_bot.get("binding_id"):
                        self._retry_creation_confirmation(existing_bot["binding_id"])
                    if existing_bot_type == "service":
                        self._backfill_service_publish_record(
                            bot_id=bot_id, user_id=user_id, bot=existing_bot
                        )
                    logger.info(
                        "[bot_service.create_bot] Bot %s already exists for user "
                        "%s, returning existing bot",
                        bot_id,
                        user_id,
                    )
                    return existing_bot
                self._initialize_capability_installations(
                    bot_id=bot_id, owner_id=user_id, bot=existing_bot
                )
                if not provision or existing_bot_type == "desktop":
                    logger.info(
                        "[bot_service.create_bot] Bot %s already recorded for user "
                        "%s, returning pending bot",
                        bot_id,
                        user_id,
                    )
                    return existing_bot
                return self.provision_bot(
                    bot_id=bot_id,
                    user_id=user_id,
                    nick_name=nick_name,
                    template_type=existing_template_type,
                    template_config=template_config,
                    cookie=cookie,
                )
            # 不存在，继续创建流程，使用传入的 bot_id
            logger.info(f"[bot_service.create_bot] Using provided bot_id={bot_id} for user {user_id}")
        else:
            # 未传入 bot_id，生成新的
            bot_id = generate_bot_id(user_id, self._repository)

        # Resolve active engine before creation preflight validation.
        resolved_active_engine = engine_type or DEFAULT_ENGINE_TYPE

        # ===== 创建前置校验：数量上限 + Default Bot 引擎约束 =====
        self.check_create_bot_preflight(
            user_id=user_id,
            bot_id=bot_id,
            engine_type=resolved_active_engine,
            space_id=space_id,
            space_quota=space_quota,
        )

        # Resolve entity info
        resolved_entity_id = entity_id or f"staff_{user_id}"
        resolved_entity_type = entity_type or "staff"

        # Always use backend configured engine types, ignore frontend input.
        # _get_engine_types() (ENGINE_TYPES env, falling back to the static list)
        # is what validation uses; persisting the static list instead meant a bot
        # created on a deployment-enabled engine (e.g. teclaw) stored an
        # enabled-engine list that omitted its own active engine.
        resolved_engine_types = _get_engine_types()

        # A bot's active engine must be a member of its own enabled-engine list —
        # consumers that enumerate that list (``get_engine_paths``, the bot-detail
        # read surfaces) would otherwise skip the engine the bot actually runs on.
        # The invariant held by accident while the static list was persisted (it
        # contains DEFAULT_ENGINE_TYPE); persisting the configured registry broke
        # it wherever the two differ.
        #
        # Guaranteed by construction rather than by rejecting: teclaw is a
        # supported engine that is absent from the default registry, so
        # rejecting would break teclaw creation on any deployment that does not
        # set ENGINE_TYPES. Whether an engine outside the configured registry
        # should be creatable at all is a separate question, decided by the
        # callers that validate it — not something to enforce here by writing a
        # row that contradicts itself.
        if resolved_active_engine not in resolved_engine_types:
            resolved_engine_types = [*resolved_engine_types, resolved_active_engine]
        resolved_bot_type = bot_type or "personal"

        if resolved_bot_type == "service" and not self.is_teclaw_bot(
            resolved_active_engine
        ):
            # The CommonConfig record is the master switch. Missing, disabled,
            # or lacking a valid image preserves the pre-feature behavior and
            # removes any caller-supplied image-policy fields.
            if self._service_bot_image_policy_enabled():
                ext = apply_default_image_to_ext(ext)
            else:
                ext = clear_image_policy_from_ext(ext)

        # Resolve bot name according to naming rules
        resolved_bot_name = self._resolve_bot_name(bot_name, bot_id, user_id, nick_name)

        # Check if bot_name already exists globally
        if bot_name and bot_name.strip():
            if self._repository.exists_by_bot_name(bot_name.strip()):
                raise BotNameExistsError(f"Bot name '{bot_name}' already exists")

        # The legacy route keeps its established owner/device limit. A Space-aware
        # OpenAPI create has already checked the target Space's Bot rows and must
        # not be blocked again by the owner's personal ceiling.
        quota_exempt = resolved_active_engine.strip().lower() == "teclaw"
        if not space_quota and not quota_exempt:
            self._check_device_limit(resolved_entity_id, resolved_entity_type, user_id)

        logger.info(f"[bot_service.create_bot] Creating bot {bot_id} for user {user_id}, "
                   f"entity={resolved_entity_type}/{resolved_entity_id}, engines={resolved_engine_types}, "
                   f"bot_name={resolved_bot_name}")

        try:
            # Step 1: Create bot record with PENDING status (binding_id is None initially)
            bot_data = {
                "bot_id": bot_id,
                "bot_name": resolved_bot_name,
                "bot_desc": bot_desc,
                "entity_id": resolved_entity_id,
                "entity_type": resolved_entity_type,
                "creator_id": user_id,
                "owner_id": user_id,
                "owner_name": nick_name,  # Set owner_name to creator's nick_name
                "modifier_id": user_id,
                "engine_types": resolved_engine_types,
                "active_engine": resolved_active_engine,
                "status": "PENDING",  # Initial status
                "binding_id": None,
                "device_id": None,
                "share_policy": share_policy,
                "is_delete": 0,
                "ext": ext,
                "bot_type": resolved_bot_type,
                "template_type": template_type,  # Template type (e.g., "applicationCoding")
                "space_id": space_id,  # Business-space ownership: NULL -> personal fallback
            }
            initial_layout = self._skills_pool_native_creation_policy.select(
                env=get_current_env(),
                owner_id=user_id,
                bot_id=str(bot_id),
                engine_type=resolved_active_engine,
                bot_type=resolved_bot_type,
            )

            if not space_quota or quota_exempt:
                bot_record = self._repository.insert_with_initial_skill_layout(
                    bot_data,
                    layout=initial_layout,
                )
            else:
                # Serialize only the final count + row insert. Passport and
                # device provisioning stay outside this short quota lease.
                with self._require_bot_quota_service().guard_add(
                    owner_id=user_id, space_id=space_id
                ):
                    bot_record = self._repository.insert_with_initial_skill_layout(
                        bot_data,
                        layout=initial_layout,
                    )
            logger.info(f"[bot_service.create_bot] Bot {bot_id} created with PENDING status")

            if not provision:
                self._prepare_creation_template(
                    bot_id=bot_id,
                    user_id=user_id,
                    bot_name=resolved_bot_name,
                    bot_type=resolved_bot_type,
                    active_engine=resolved_active_engine,
                    template_type=template_type,
                    template_config=template_config,
                )

            # This is deliberately DB-only and precedes both early returns.
            # New Bots must not depend on a later effective read or Runtime
            # projector to materialize their initial Default/SkillSet state.
            self._initialize_capability_installations(
                bot_id=bot_id, owner_id=user_id
            )

            # Register once per persisted create, including desktop and deferred paths.
            connection_mode = self._resolve_bcn_provider_connection_mode(
                active_engine=resolved_active_engine,
                bot_type=resolved_bot_type,
            )
            logger.info(
                f"[bot_service.create_bot] register bot to BCN as provider: "
                f"bot_id={bot_id} active_engine={resolved_active_engine} "
                f"bot_type={resolved_bot_type} template_type={template_type} "
                f"connection_mode={connection_mode}"
            )
            try:
                self._register_bot_to_bcn_as_provider(
                    bot_id=bot_id,
                    user_id=user_id,
                    owner_workno=user_id,
                    bot_name=resolved_bot_name or bot_id,
                    bot_summary=bot_desc or "",
                    connection_mode=connection_mode,
                    avatar_url=outbound_avatar_from_record(
                        bot_record, log_context="[bot_service.create_bot]"
                    ),
                    **provider_address_kwargs(bot_data),
                    respect_drm=False,
                )
            except Exception as e:
                logger.warning(
                    f"[bot_service.create_bot] BCN provider registration failed for bot {bot_id}, "
                    f"connection_mode={connection_mode}, "
                    f"error_type={type(e).__name__}, will retry on next start"
                )

            # 桌面 bot 的设备分配走 BaaS 流程（DesktopBotService._execute_creation），
            # 不应走 DeviceService.apply_device()（会生成 staff_xxx 格式 device_id）。
            if resolved_bot_type == "desktop":
                logger.info(
                    f"[bot_service.create_bot] Bot {bot_id} is desktop type, "
                    f"skipping DeviceService.apply_device (uses BaaS allocation)"
                )
                return bot_record

            if not provision:
                # W8: the record is the whole first step. The device, the
                # publish record and the rest wait for ``provision_bot``,
                # which the creation job calls once the manifest's
                # pre-container phase has written the platform state the
                # first artifact is composed from.
                logger.info(
                    f"[bot_service.create_bot] Bot {bot_id} recorded; provisioning deferred"
                )
                return bot_record

            return self.provision_bot(
                bot_id=bot_id,
                user_id=user_id,
                nick_name=nick_name,
                template_type=template_type,
                template_config=template_config,
                cookie=cookie,
            )

        except (BotServiceError, BotQuotaError):
            raise
        except Exception as e:
            logger.error(f"[bot_service.create_bot] Failed to create bot record: {e}")
            raise BotServiceError(f"Failed to create bot record: {e}")

    def _prepare_creation_template(
        self,
        *,
        bot_id: str,
        user_id: str,
        bot_name: str,
        bot_type: str,
        active_engine: str,
        template_type: str | None,
        template_config: dict[str, Any] | None,
    ) -> None:
        """Idempotently finish the pre-provision template step for retries."""

        if template_type is None or template_config is None:
            return
        if self._template_service.exists_template(bot_id):
            return
        from agentclaw.community.core.bot_management.engines import (
            HostedWorkspaceNotEligibleError,
            HostedWorkspaceProvisioningError,
            resolve_provisioning,
        )

        provision_ctx, provision_strategy = resolve_provisioning(
            bot_id=bot_id,
            owner_id=user_id,
            bot_type=bot_type,
            active_engine=active_engine,
            template_type=template_type,
            template_config=template_config,
        )
        hosted_workspace_eligible = True
        try:
            workspace_id = provision_strategy.ensure_hosted_workspace(
                provision_ctx,
                bot_name=bot_name,
                template_config=template_config,
                template_service=self._template_service,
                workspace_hosting_provider=self._require_workspace_hosting,
            )
        except HostedWorkspaceNotEligibleError:
            hosted_workspace_eligible = False
            workspace_id = None
        except HostedWorkspaceProvisioningError as exc:
            raise BotServiceError(
                "applicationCoding workspace creation returned no id"
            ) from exc
        except Exception as exc:
            raise BotServiceError(
                "applicationCoding workspace creation failed"
            ) from exc
        if hosted_workspace_eligible and not workspace_id:
            raise BotServiceError(
                "applicationCoding workspace creation returned no id"
            )
        if workspace_id and self._template_service.exists_template(bot_id):
            return
        try:
            self._template_service.create_template(
                bot_id=bot_id,
                template_config=template_config,
                template_type=template_type,
                active_engine=active_engine,
            )
        except Exception as exc:
            logger.error(
                "[bot_service.create_bot] Failed to create template for bot %s: %s",
                bot_id,
                exc,
                exc_info=True,
            )
            if workspace_id:
                raise BotServiceError(
                    "applicationCoding template creation failed"
                ) from exc

    def provision_bot(
        self,
        bot_id: str,
        user_id: str,
        nick_name: str,
        *,
        template_type: Optional[str] = None,
        template_config: Optional[Dict[str, Any]] = None,
        cookie: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run step 2 and its tail for a bot created with ``provision=False``.

        Idempotent, durably: a record that already carries a binding is
        returned as it is, and before any of step 2's work begins the record is
        moved from ``PENDING`` to ``PROVISIONING`` by one conditional update
        (``claim_provisioning``). A second call that finds the claim taken —
        an at-least-once re-invocation of the creation job while the first is
        still allocating — returns the record untouched rather than allocating
        a second device. A claim left behind by a holder that died (still
        unbound after ``PROVISIONING_CLAIM_STALE_SECONDS`` on the database
        clock) is taken over instead, so the record does not stay stranded at
        ``PROVISIONING``. Everything after the record — BCN registration, the
        device or teclaw container, the publish record, the draft artifact,
        memory initialisation — runs exactly as ``create_bot`` runs it, from
        the same code, so ``create_bot()`` and ``create_bot(provision=False)``
        followed by this are the same writes in the same order (W8).

        ``template_type`` / ``template_config`` / ``cookie`` are not on the
        record (the template row is, but the config the tail reads is the
        caller's); the caller that deferred provisioning still holds them.
        """
        record = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not record:
            raise BotServiceError(f"Bot {bot_id} not found for user {user_id}")
        if record.get("binding_id"):
            self._retry_creation_confirmation(record["binding_id"])
            logger.info(
                f"[bot_service.provision_bot] Bot {bot_id} already provisioned "
                f"(binding_id={record.get('binding_id')})"
            )
            return record

        if not self._repository.claim_provisioning(
            bot_id, user_id, reclaim_after_seconds=PROVISIONING_CLAIM_STALE_SECONDS
        ):
            logger.info(
                f"[bot_service.provision_bot] Bot {bot_id} is being provisioned by "
                f"another call (status={record.get('status')}); returning the record"
            )
            return record

        engine_types = record.get("engine_types") or _get_engine_types()
        if isinstance(engine_types, str):
            engine_types = [engine_types]
        active_engine = record.get("active_engine") or DEFAULT_ENGINE_TYPE
        engine_types = list(engine_types)
        if active_engine not in engine_types:
            engine_types.append(active_engine)
        try:
            self._prepare_creation_template(
                bot_id=bot_id,
                user_id=user_id,
                bot_name=record.get("bot_name") or bot_id,
                bot_type=str(record.get("bot_type") or "personal"),
                active_engine=active_engine,
                template_type=template_type or record.get("template_type"),
                template_config=template_config,
            )
            return self._provision_created_bot(
                dict(record),
                bot_id=bot_id,
                user_id=user_id,
                nick_name=nick_name,
                bot_desc=record.get("bot_desc"),
                resolved_bot_name=record.get("bot_name"),
                resolved_entity_id=str(record.get("entity_id") or f"staff_{user_id}"),
                resolved_entity_type=str(record.get("entity_type") or "staff"),
                resolved_engine_types=engine_types,
                resolved_active_engine=active_engine,
                resolved_bot_type=str(record.get("bot_type") or "personal"),
                template_type=template_type or record.get("template_type"),
                template_config=template_config,
                cookie=cookie,
            )
        except BotServiceError:
            self._release_provisioning_claim(bot_id, user_id)
            raise
        except Exception as e:
            logger.error(f"[bot_service.provision_bot] Failed to provision bot {bot_id}: {e}")
            self._release_provisioning_claim(bot_id, user_id)
            raise BotServiceError(f"Failed to provision bot: {e}")

    def _retry_creation_confirmation(self, binding_id: int) -> None:
        """Retry only the retained startup callback, not device provisioning."""
        try:
            self._device_service_provider().retry_creation_confirmation(binding_id=binding_id)
        except DeviceServiceError as exc:
            raise BotServiceError(f"Failed to confirm provisioned bot: {exc}") from exc
