"""
Runtime/capability initialization, engine-extra-env shaping and restart-lock
acquisition — moved verbatim from ``bot_service.py``.

Part of the mechanical split of ``bot_service.py``: ``BotRuntimeSetupMixin`` members were
moved here verbatim and the facade composes them onto ``BotService``.
"""

from typing import Any, Dict, List, Optional

from agentclaw.community.core.bot_collaborator.models import CollaboratorRole
from agentclaw.community.core.bot_management.capabilities import is_template_factory_config
from agentclaw.community.core.bot_management.engines.aicoding.strategy import AICODING_ENGINE_TYPE
from agentclaw.community.core.bot_management.engines.registry import (
    normalize_engine_type,
    resolve_baas_engine_bucket,
)
from agentclaw.community.core.bot_management.repository.models import BotRestartLockRecord
from agentclaw.community.core.devices.services.device_service import (
    ARCA_DEVICE_PROVIDER,
    BAAS_DEVICE_PROVIDER,
)
from agentclaw.community.core.service_bot.services.arca_image_pin import (
    apply_default_image_to_ext,
    clear_image_policy_from_ext,
    persist_default_image_policy,
    resolve_current_arca_image,
)
from agentclaw.community.core.service_bot.services.publish_exceptions import (
    PublishAlreadyExistsError,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.utils.env_utils import get_current_env

from .common import (
    BotNotFoundError,
    BotServiceError,
    RESTART_LOCK_TTL_SECONDS,
    logger,
)


class BotRuntimeSetupMixin:
    """Runtime/capability initialization, engine-extra-env shaping and restart-lock
    acquisition."""

    def _initialize_capability_installations(
        self, *, bot_id: str, owner_id: str, bot: "Dict[str, Any] | None" = None
    ) -> None:
        """Persist a new Bot's DB-only capability projection before returning.

        No Runtime projection or device readiness check is involved here.
        """
        self._skill_set_factory.initialize_installations(
            bot_id=bot_id, owner_id=owner_id, bot=bot
        )

    def _backfill_service_publish_record(
        self, *, bot_id: str, user_id: str, bot: Dict[str, Any]
    ) -> None:
        """为已存在的 service bot 补建缺失的发布单（幂等收敛）。

        直接创建即服务（一步式直建）的发布单是调用方发布编排的载体：
        create 时持久化失败会抛错（调用方重试时软删后的行一般不存在，
        走全新创建），但并发窗口或历史上吞错的存量行可能留下"service
        bot 无发布单"的可恢复状态——重放 create 的重试在这里收敛它，
        而不是返回一个无法发布的假成功。补齐失败照常上抛，让重试继续
        可见；另一事务已建时以幂等收尾。
        """
        try:
            existing = self._bot_publish_repo.get_by_publish_bot_id(
                bot_id, user_id, get_current_env()
            )
            if existing is not None:
                return
            self._bot_publish_provider().create_first_publish_for_bot(
                bot_id=bot_id,
                owner_id=user_id,
                name=bot.get("bot_name") or bot_id,
                permission_owner="owner",
                description=bot.get("bot_desc"),
                owner_name=bot.get("owner_name"),
            )
            logger.info(
                "[bot_service.create_bot] Backfilled publish record for service "
                "bot %s",
                bot_id,
            )
        except PublishAlreadyExistsError:
            # 并发重放：另一事务先建好了发布单。
            logger.info(
                "[bot_service.create_bot] Publish record already exists for "
                "service bot %s",
                bot_id,
            )
        except Exception as e:
            raise BotServiceError(
                f"Failed to backfill publish record for service bot {bot_id}"
            ) from e

    def _service_bot_image_policy_enabled(self) -> bool:
        """Whether draft create/restart should opt into image policy."""
        return (
            resolve_current_arca_image(
                getattr(self, "_common_config_service", None),
                env=get_current_env(),
            )
            is not None
        )

    def _mark_service_bot_default_image(
        self,
        bot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply the draft ARCA image policy only when fully configured."""
        if bot.get("bot_type") != "service" or self.is_teclaw_bot(
            bot.get("active_engine")
        ):
            return bot
        updated_bot = dict(bot)
        if self._service_bot_image_policy_enabled():
            updated_bot["ext"] = apply_default_image_to_ext(bot.get("ext"))
        else:
            updated_bot["ext"] = clear_image_policy_from_ext(bot.get("ext"))
        return updated_bot

    def _persist_service_bot_default_image(
        self,
        bot: Dict[str, Any],
        *,
        user_id: str,
    ) -> None:
        """Persist an accepted draft restart's default policy to Bot and Draft."""
        if (
            bot.get("bot_type") != "service"
            or self.is_teclaw_bot(bot.get("active_engine"))
            or not self._service_bot_image_policy_enabled()
        ):
            return

        persist_default_image_policy(
            bot_repository=self._repository,
            publish_repository=self._bot_publish_repo,
            bot_id=str(bot["bot_id"]),
            owner_id=user_id,
            env=get_current_env(),
            common_config_service=self._common_config_service,
        )

    def _build_engine_extra_envs(
        self,
        *,
        bot_id: str,
        owner_id: str,
        active_engine: "str | None",
        bot_type: str,
        template_type: "str | None",
        template_config: "Optional[Dict[str, Any]]",
        log_context: str,
    ) -> "Optional[Dict[str, Any]]":
        """Build engine-specific extra_envs via the provisioning strategy.

        Centralizes the create / restart / start provisioning so each call site
        only supplies the metadata it actually has.  Fails soft: any strategy
        error is logged and treated as "no extra envs" so device allocation is
        never blocked by the engine layer.  Logging is engine-agnostic (logs the
        whole ``extra_envs`` dict) so new engine strategies are picked up without
        touching bot_service.
        """
        try:
            from agentclaw.community.core.bot_management.engines import (
                resolve_provisioning,
            )

            ctx, strategy = resolve_provisioning(
                bot_id=bot_id,
                owner_id=owner_id,
                active_engine=active_engine,
                bot_type=bot_type,
                template_type=template_type,
                template_config=template_config,
            )
            extra_envs = strategy.build_extra_envs(ctx)
            if extra_envs:
                logger.info(
                    "[%s] Setting engine extra_envs for bot %s: %s",
                    log_context,
                    bot_id,
                    extra_envs,
                )
            return extra_envs
        except Exception as e:
            logger.warning(
                "[%s] Failed to build engine extra_envs for bot %s: %s",
                log_context,
                bot_id,
                e,
            )
            return None

    def _skills_layout_env(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
    ) -> dict[str, str]:
        state = self._skill_layout_repository.get(
            BotSkillLayoutScope(env=env, entity_id=entity_id, bot_id=bot_id)
        )
        if state.active_layout is not SkillLayout.POOL:
            return {"AGENTCLAW_SKILLS_LAYOUT": SkillLayout.LEGACY.value}
        contract = state.layout_contract_version
        if not isinstance(contract, str) or not contract:
            raise BotServiceError("Pool layout has no contract version")
        return {
            "AGENTCLAW_SKILLS_LAYOUT": SkillLayout.POOL.value,
            "AGENTCLAW_SKILLS_LAYOUT_CONTRACT_VERSION": contract,
            "AGENTCLAW_SKILLS_LAYOUT_PHASE": (
                state.phase.value
                if state.phase
                in {
                    SkillLayoutPhase.POOL_INITIALIZING,
                    SkillLayoutPhase.POOL_ACTIVE,
                }
                else "recovery_required"
            ),
        }

    def _extract_engine_runtime_token(
        self,
        *,
        bot_id: str,
        owner_id: str,
        active_engine: "str | None",
        bot_type: str,
        template_type: "str | None",
        template_config: "Optional[Dict[str, Any]]",
        log_context: str,
    ) -> "Optional[str]":
        """Resolve the engine runtime token (symmetric to ``_build_engine_extra_envs``).

        Used by the update_bot token-refresh path so it shares the same
        ``resolve_provisioning`` entry point instead of rebuilding context +
        strategy inline.  Fails soft (returns None).
        """
        try:
            from agentclaw.community.core.bot_management.engines import (
                resolve_provisioning,
            )

            ctx, strategy = resolve_provisioning(
                bot_id=bot_id,
                owner_id=owner_id,
                active_engine=active_engine,
                bot_type=bot_type,
                template_type=template_type,
                template_config=template_config,
            )
            token = strategy.extract_runtime_token(ctx)
            if token:
                logger.info(
                    "[%s] Resolved engine runtime token for bot %s",
                    log_context,
                    bot_id,
                )
            return token
        except Exception as e:
            logger.warning(
                "[%s] Failed to extract engine runtime token for bot %s: %s",
                log_context,
                bot_id,
                e,
            )
            return None

    @staticmethod
    def _should_trigger_memory_initialization(
        *,
        active_engine: "str | None",
        template_type: "str | None",
        template_config: "Optional[Dict[str, Any]]",
        old_template_config: "Optional[Dict[str, Any]]" = None,
        on_create: bool = False,
    ) -> bool:
        """Whether to reuse the AppCoding memory/Wiki initialization path.

        Template-factory bots consume business Wiki / RepoWiki through the same
        AppCoding runtime pipeline, but the source of truth is AC resolved
        ``template_config``.  Keep applicationCoding legacy behavior on create,
        and let template-factory bots trigger only when their template snapshot
        actually declares repo/wiki sources.
        """
        if active_engine != "claude_code" or not isinstance(template_config, dict):
            return False

        # Legacy applicationCoding keeps the original always-init-on-create
        # behavior.  Template-factory bots (normalCC / architect / user-created)
        # are detected from the resolved template snapshot instead of a backend
        # template_type whitelist, and only initialize memory when repo/wiki
        # sources are declared.
        is_legacy_application_coding = template_type == "applicationCoding"
        is_template_factory_bot = is_template_factory_config(template_config)
        if not is_legacy_application_coding and not is_template_factory_bot:
            return False

        try:
            from agentclaw.community.core.bot_management.utils import (
                memory_sources_changed,
            )

            empty_config: Dict[str, Any] = {}
            has_sources = memory_sources_changed(empty_config, template_config)
            if on_create:
                return is_legacy_application_coding or has_sources
            return memory_sources_changed(old_template_config or empty_config, template_config)
        except Exception as e:
            logger.warning(
                "[bot_service.memory_init] source detection failed: "
                "template_type=%s active_engine=%s error=%s",
                template_type,
                active_engine,
                e,
            )
            return template_type == "applicationCoding" and on_create

    def resolve_bot_template_uid(self, *, bot_id: str, user_id: str, env: str) -> str:
        """Resolve a stored bot's template identity without selecting a template UUID.

        Lookup/resolution failures propagate: callers must not mistake an unknown
        template for one eligible for the legacy ARCA image Pin.
        """
        from agentclaw.community.core.bot_management.addressing import BotAddressResolver
        from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError

        try:
            bot = dict(BotAddressResolver(self._repository).resolve(
                bot_id=bot_id, legacy_owner_id=user_id,
            ).bot)
        except BotAddressNotFoundError as exc:
            raise BotNotFoundError(f"Bot not found: {bot_id}") from exc
        # Unlike get_bot's best-effort enrichment, propagate template read errors.
        # Identity lookup does not need device bindings.
        template_config = self._template_service.get_template_config_strict(bot_id)
        return self._baas_template_resolver.resolve_template_uid(
            bot_id=bot_id,
            user_id=str(bot["owner_id"]),
            env=env,
            bot_type=str(bot.get("bot_type") or "service"),
            engine_type=bot.get("active_engine"),
            template_type=bot.get("template_type"),
            template_config=template_config if isinstance(template_config, dict) else None,
        )

    def _attach_template_uid_context(
        self,
        *,
        bot_id: str,
        user_id: str,
        bot_type: str,
        engine_type: str | None,
        template_type: str | None,
        template_config: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """为设备分配补上 template_uid 上下文。

        这里还没确定最终 provider，因此只记录解析结果，不直接阻断流程。
        如果后面路由到 ARCA，这些信息不会影响分配；如果路由到 BaaS，
        BaasDeviceService 会再校验 template_uid，并基于这里记录的错误 fail-fast。
        """
        template_resolver = getattr(self, "_baas_template_resolver", None)
        if template_resolver is None:
            return template_config

        resolved_template_config = dict(template_config or {})
        try:
            template_uid = template_resolver.resolve_template_uid(
                bot_id=bot_id,
                user_id=user_id,
                env=get_current_env(),
                bot_type=bot_type,
                engine_type=engine_type,
                template_type=template_type,
                template_config=template_config,
            )
            resolved_template_config["template_uid"] = template_uid
            resolved_template_config.pop("_baas_template_uid_resolution_error", None)
            logger.info(
                "[bot_service.template_uid] attached: bot_id=%s user_id=%s "
                "bot_type=%s engine_type=%s template_type=%s template_uid=%s",
                bot_id,
                user_id,
                bot_type,
                engine_type,
                template_type,
                template_uid,
            )
        except Exception as e:
            resolved_template_config["template_uid"] = None
            resolved_template_config["_baas_template_uid_resolution_error"] = str(e)
            logger.warning(
                "[bot_service.template_uid] resolution failed: bot_id=%s user_id=%s "
                "bot_type=%s engine_type=%s template_type=%s error=%s",
                bot_id,
                user_id,
                bot_type,
                engine_type,
                template_type,
                e,
            )
        return resolved_template_config

    def _resolve_baas_restart_template_uuid(
        self,
        *,
        bot_id: str,
        user_id: str,
        bot: dict[str, Any],
        template_config: dict[str, Any] | None,
    ) -> str | None:
        """BaaS 原地重启前，按当前 bot 上下文解析 template_uuid。

        BaaS update 仍然会重建 create payload；这里显式传入 template_uuid，
        避免 BaasService 使用默认模板覆盖当前引擎应使用的模板。
        """
        template_resolver = getattr(self, "_baas_template_resolver", None)
        if template_resolver is None:
            return None

        env = get_current_env()
        if not isinstance(template_config, dict):
            template_config = None

        template = template_resolver.resolve_template(
            bot_id=bot_id,
            user_id=user_id,
            env=env,
            bot_type=str(bot.get("bot_type") or ""),
            engine_type=bot.get("active_engine"),
            template_type=bot.get("template_type"),
            template_config=template_config,
        )
        logger.info(
            "[bot_service.restart_template] resolved: bot_id=%s user_id=%s "
            "engine_type=%s template_type=%s template_uid=%s template_uuid=%s",
            bot_id,
            user_id,
            bot.get("active_engine"),
            bot.get("template_type"),
            template.template_uid,
            template.template_uuid,
        )
        return template.template_uuid

    def _resolve_restart_target_provider(
        self,
        *,
        bot_id: str,
        user_id: str,
        bot: dict[str, Any],
        source_provider: str | None,
    ) -> str | None:
        """Resolve an opt-in provider migration for an existing ARCA bot.

        The database-backed template override is a positive-match switch. Any
        absent, invalid, unmatched, or unreadable configuration preserves the
        source provider and therefore the pre-existing restart behavior.
        """
        if source_provider != ARCA_DEVICE_PROVIDER:
            return source_provider

        active_engine = normalize_engine_type(bot.get("active_engine"), default="")
        template_type = bot.get("template_type")
        template_config = bot.get("template_config")
        engine_bucket = resolve_baas_engine_bucket(
            engine_type=active_engine,
            template_type=template_type,
            template_config=template_config
            if isinstance(template_config, dict)
            else None,
        )
        if active_engine not in {"openclaw", "hermes", "claude_code"}:
            return source_provider
        if engine_bucket == AICODING_ENGINE_TYPE:
            return source_provider

        template_resolver = getattr(self, "_baas_template_resolver", None)
        if template_resolver is None:
            return source_provider

        owner_id = str(bot.get("owner_id") or user_id)
        try:
            override_uuid = template_resolver.resolve_template_override(
                env=get_current_env(),
                user_id=owner_id,
                bot_type=str(bot.get("bot_type") or "personal"),
            )
        except Exception as e:
            logger.warning(
                "[bot_service.restart_provider] template override lookup failed; "
                "preserving source provider: bot_id=%s owner_id=%s "
                "source_provider=%s error=%s",
                bot_id,
                owner_id,
                source_provider,
                e,
            )
            return source_provider

        if override_uuid is None:
            logger.info(
                "[bot_service.restart_provider] no template override hit; "
                "preserving source provider: bot_id=%s owner_id=%s "
                "source_provider=%s",
                bot_id,
                owner_id,
                source_provider,
            )
            return source_provider

        logger.info(
            "[bot_service.restart_provider] template override hit; migrating provider: "
            "bot_id=%s owner_id=%s source_provider=%s target_provider=%s "
            "template_uuid=%s active_engine=%s template_type=%s",
            bot_id,
            owner_id,
            source_provider,
            BAAS_DEVICE_PROVIDER,
            override_uuid,
            active_engine,
            template_type,
        )
        return BAAS_DEVICE_PROVIDER

    def _get_device_binding_repo(self):
        """Get the DeviceBindingRepository (injected via __init__)."""
        return self._device_binding_repo

    def _query_admin_worknos(self, bot_id: str, owner_id: str) -> list[str] | None:
        """Query admin collaborator worknos for a bot.

        Returns ``[]`` when the bot has no admins, and ``None`` only when the
        query itself fails. Callers downstream (passport updatePassport) treat
        ``[]`` as "clear admins" and ``None`` as "do not update", so the two
        cases must stay distinct.
        """
        try:
            env = get_current_env()
            collaborators = self._collaborator_repo.list_by_bot(
                bot_id=bot_id, owner_id=owner_id, env=env, role=CollaboratorRole.ADMIN,
            )
            return [c.user_id for c in collaborators]
        except Exception as e:
            logger.warning(
                "[bot_service._query_admin_worknos] Failed: bot_id=%s, owner_id=%s, error=%s",
                bot_id, owner_id, e,
            )
            return None

    def _list_bot_members(
        self, bot_id: str, owner_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        """List a bot's members (collaborators) for the appcoding-bots response.

        Reads the ``ac_bot_collaborator`` table (added admin/member
        collaborators) scoped to the current env. The bot owner is intentionally
        not synthesized as a member here: owner identity lives on the bot record
        itself (``owner_id`` / ``owner_name``); callers wanting the full roster
        should combine that with this list.

        Returns an empty list when there are no collaborators, when ``owner_id``
        is missing, or when the lookup itself fails — member enrichment must
        never break the coding-bots listing.

        Args:
            bot_id: Bot ID of the coding bot.
            owner_id: Bot owner's work number (filters the collaborator index).

        Returns:
            A list of member dicts with ``user_id`` and ``user_name`` only
            (operator_id / role / timestamps are not exposed on this public
            surface).
        """
        if not owner_id:
            return []
        try:
            env = get_current_env()
            collaborators = self._collaborator_repo.list_by_bot(
                bot_id=bot_id, owner_id=owner_id, env=env,
            )
            return [
                {"user_id": c.user_id, "user_name": c.user_name}
                for c in collaborators
            ]
        except Exception as e:
            logger.warning(
                "[bot_service._list_bot_members] Failed: bot_id=%s, owner_id=%s, error=%s",
                bot_id, owner_id, e,
            )
            return []

    def _try_acquire_restart_lock(
        self, env: str, entity_id: str, bot_id: str, holder_user_id: str
    ) -> Optional[BotRestartLockRecord]:
        """Try to acquire the per-bot restart idempotency lock.

        The lock is a row in ``ac_bot_restart_lock`` guarded by a UNIQUE
        constraint on ``(env, entity_id, bot_id)``:

        1. Attempt ``INSERT`` — success means we hold the lock.
        2. On conflict, if the existing row is stale (older than
           ``RESTART_LOCK_TTL_SECONDS``, judged on the DB clock) the holder is
           presumed crashed: delete the stale row and re-``INSERT``.
        3. Otherwise a restart is genuinely in progress → return ``None`` so
           the caller suppresses this duplicate.

        Returns the lock record on success, or ``None`` if a restart is already
        in progress for this bot.
        """
        rec = self._restart_lock_repo.acquire(env, entity_id, bot_id, holder_user_id)
        if rec is not None:
            return rec

        # A row existed at INSERT time. Since acquire/check/acquire is not
        # transactional, the row may since have (a) stayed held & fresh, (b)
        # become stale, or (c) been released by its holder. If it's stale, reap
        # it; then make a SECOND, authoritative acquire attempt that decides the
        # outcome:
        #   - reaped (b) or released (c)  -> key is now free -> acquire succeeds.
        #   - still held & fresh (a), or re-taken by another worker -> acquire
        #     fails -> None -> suppress this duplicate.
        # Crucially this also covers case (c): if the previous holder released
        # between our first acquire and the staleness check, get_if_stale returns
        # None (no row) yet the second acquire still picks the lock up — so a
        # legitimately-new restart is not dropped as a phantom duplicate.
        stale = self._restart_lock_repo.get_if_stale(
            env, entity_id, bot_id, RESTART_LOCK_TTL_SECONDS
        )
        if stale is not None:
            logger.warning(
                "[bot_service._try_acquire_restart_lock] Reaping stale restart lock: "
                "env=%s, entity_id=%s, bot_id=%s, created=%s",
                env, entity_id, bot_id, stale.gmt_create,
            )
            # Compare-and-delete on the stale row's token: if another worker
            # already reaped+reacquired, the token won't match and this is a
            # no-op — the acquire below then fails and we suppress, instead of
            # stealing their fresh lock.
            self._restart_lock_repo.release(env, entity_id, bot_id, stale.lock_token)

        return self._restart_lock_repo.acquire(env, entity_id, bot_id, holder_user_id)
