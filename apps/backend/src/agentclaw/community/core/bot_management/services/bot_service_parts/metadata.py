"""
``update_bot`` / ``admin_update_bot``, codefuse-token refresh and the trailing
``update_bot_ext`` — moved verbatim from ``bot_service.py``.

Part of the mechanical split of ``bot_service.py``: ``BotMetadataMixin`` members were
moved here verbatim and the facade composes them onto ``BotService``.
"""

import json
import threading

from typing import Any, Dict, Optional

from agentclaw.community.core.bot_management.codefuse_write import (
    CodefuseTargetResult,
    CodefuseWriteOutcome,
    CodefuseWriteResult,
)
from agentclaw.community.core.bot_management.expert_origin import reject_expert_origin_update
from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

from .common import (
    BotNameExistsError,
    BotNotFoundError,
    BotServiceError,
    logger,
)


class BotMetadataMixin:
    """``update_bot`` / ``admin_update_bot``, codefuse-token refresh and the trailing
    ``update_bot_ext``."""

    def update_bot(
        self,
        bot_id: str,
        user_id: str,
        bot_name: Optional[str] = None,
        bot_desc: Optional[str] = None,
        share_policy: Optional[Dict[str, Any]] = None,
        ext: Optional[Dict[str, Any]] = None,
        template_config: Optional[Dict[str, Any]] = None,
        sync_to_bcn: bool = True,
        request_headers: Optional[Dict[str, str]] = None,
        cookie: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update bot information.

        Args:
            bot_id: Bot ID
            user_id: User ID (used as modifier and for permission check)
            bot_name: New bot name (optional)
            bot_desc: New bot description (optional)
            share_policy: New sharing configuration (optional)
            ext: Extension field dictionary (optional), e.g., {"avatar_url": "https://..."}
            template_config: Template configuration dictionary (optional)
            sync_to_bcn: Whether to sync to BCN (default: True)
            request_headers: Incoming HTTP request headers for downstream sync (optional)

        Returns:
            Updated bot record

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If update fails
        """
        # Check user_id is provided
        if not user_id:
            raise BotServiceError("User ID is required for updating bot")

        # Get bot by bot_id and owner_id (user_id)
        reject_expert_origin_update(ext)
        bot = self._repository.get_by_id_and_owner(bot_id, user_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        update_data = {}

        if bot_name is not None:
            # Check if new bot_name already exists (and it's not the current bot)
            if bot_name.strip():
                existing_bot = self._repository.get_by_bot_name(bot_name.strip())
                # Identify the current record by owner AND bot_id. bot_id is only
                # unique per owner — every owner's first bot is "default" — so
                # comparing bot_id alone made another owner's "default" bot look
                # like this one, letting its name be taken even though create and
                # check-name enforce the name tenant-wide.
                if existing_bot and not (
                    existing_bot.get("bot_id") == bot_id
                    and existing_bot.get("owner_id") == bot.get("owner_id")
                ):
                    raise BotNameExistsError(f"Bot name '{bot_name}' already exists")
            update_data["bot_name"] = bot_name
        if bot_desc is not None:
            update_data["bot_desc"] = bot_desc
        if share_policy is not None:
            update_data["share_policy"] = share_policy
        if ext is not None:
            # Merge ext with existing ext (if any)
            existing_ext = bot.get("ext") or {}
            if isinstance(existing_ext, str):
                try:
                    existing_ext = json.loads(existing_ext)
                except json.JSONDecodeError:
                    existing_ext = {}
            merged_ext = {**existing_ext, **ext}
            update_data["ext"] = merged_ext

        # Update template configuration if provided
        if template_config is not None:
            # 在更新前抓取旧 template_config，用于对比 yuque_kb_repos 是否发生变化
            old_template_config: Dict[str, Any] = {}
            try:
                old_template_config = self._template_service.get_template_config(bot_id) or {}
            except Exception as e:
                logger.warning(
                    f"[bot_service.update_bot] Failed to fetch old template_config for bot {bot_id}: {e}"
                )

            try:
                # Check if template exists
                if self._template_service.exists_template(bot_id):
                    # Update existing template
                    self._template_service.update_template(
                        bot_id=bot_id,
                        template_config=template_config,
                        template_type=bot.get("template_type"),
                        active_engine=bot.get("active_engine"),
                    )
                else:
                    # Create new template (this should not normally happen in update)
                    # But we handle it gracefully
                    self._template_service.create_template(
                        bot_id=bot_id,
                        template_config=template_config,
                        template_type=bot.get("template_type"),
                        active_engine=bot.get("active_engine"),
                    )
                logger.info(f"[bot_service.update_bot] Template updated for bot {bot_id}")

                # Runtime token 变化时按引擎策略刷新运行中容器。仅当本次入参
                # 携带 token 字段且与旧值解密后不同才触发；异步执行，失败只告警
                # 不阻断主流程。
                runtime_token = self._extract_engine_runtime_token(
                    bot_id=bot_id,
                    owner_id=bot.get("owner_id") or user_id,
                    active_engine=bot.get("active_engine"),
                    bot_type=bot.get("bot_type") or "",
                    template_type=bot.get("template_type"),
                    template_config=template_config if isinstance(template_config, dict) else None,
                    log_context="bot_service.update_bot",
                )
                if (
                    isinstance(template_config, dict)
                    and "token" in template_config
                    and runtime_token is not None
                ):
                    self._maybe_refresh_codefuse_token_async(
                        bot_id=bot_id,
                        user_id=user_id,
                        old_template_config=old_template_config,
                        new_template_config=template_config,
                    )
            except Exception as e:
                logger.error(f"[bot_service.update_bot] Failed to update template for bot {bot_id}: {e}")
                # Don't fail bot update if template update fails
                # Just log the error

            # applicationCoding + claude_code 引擎的 Bot：若语雀知识库或代码仓库发生变化，重新初始化 memory
            try:
                bot_template_type = bot.get("template_type")
                bot_active_engine = bot.get("active_engine")
                from agentclaw.community.core.bot_management.utils import (
                    trigger_memory_initialization,
                )

                if self._should_trigger_memory_initialization(
                    active_engine=bot_active_engine,
                    template_type=bot_template_type,
                    template_config=template_config,
                    old_template_config=old_template_config,
                ):
                    logger.info(
                        f"[bot_service.update_bot] memory sources changed for bot {bot_id}, "
                        f"re-triggering memory initialization"
                    )
                    trigger_memory_initialization(
                        bot_id=bot_id,
                        bot_name=bot.get("bot_name") or "",
                        user_id=user_id,
                        template_config=template_config,
                        cookie=cookie or "",
                        aixcore_base_url=self._workspace_hosting_config.aixcore_base_url,
                    )
            except Exception as e:
                logger.error(
                    f"[bot_service.update_bot] Failed to re-trigger memory init for bot {bot_id}: {e}"
                )

            # applicationCoding Bot：若 devflow_workflow 发生变化，更新 autoInitiate 定时任务的 workflow
            try:
                bot_template_type = bot.get("template_type")
                if bot_template_type == "applicationCoding":
                    from agentclaw.community.core.bot_management.utils import extract_workflow_name

                    old_wf = extract_workflow_name(old_template_config)
                    new_wf = extract_workflow_name(template_config)
                    if old_wf != new_wf:
                        import threading

                        def _update_cron_workflow(
                            _bot_id=str(bot_id), _user_id=user_id, _new_wf=new_wf,
                            _cron_provider=self._cron_auto_setup_provider,
                        ):
                            import asyncio

                            try:
                                cron_svc = _cron_provider()
                                asyncio.run(
                                    cron_svc.update_auto_initiate_workflow(
                                        bot_id=_bot_id,
                                        owner_id=_user_id,
                                        nick_name=_user_id,
                                        new_workflow_name=_new_wf,
                                    )
                                )
                            except Exception as exc:
                                logger.warning(
                                    f"[bot_service.update_bot] Failed to update cron workflow "
                                    f"for bot {_bot_id}: {exc}"
                                )

                        threading.Thread(
                            target=bind_current_avernet_tenant(_update_cron_workflow),
                            name=f"cron-workflow-update-{bot_id}",
                            daemon=True,
                        ).start()
                        logger.info(
                            f"[bot_service.update_bot] Workflow changed from '{old_wf}' to '{new_wf}' "
                            f"for bot {bot_id}, updating cron task in background"
                        )
            except Exception as e:
                logger.error(
                    f"[bot_service.update_bot] Failed to update cron workflow for bot {bot_id}: {e}"
                )

        if not update_data:
            # No fields to update, return current record with binding info
            return self.get_bot(bot_id, user_id)

        update_data["modifier_id"] = user_id

        try:
            # Use update_by_owner to ensure we only update the owner's bot
            bot = self._repository.update_by_owner(bot_id, user_id, update_data)
            if not bot:
                raise BotNotFoundError(f"Bot not found: {bot_id}")

            # Sync to BCN if bot_name, bot_desc, or the avatar field was
            # updated. Avatar presence in the request ext is the signal — not a
            # stored-value comparison, which cannot distinguish an explicit
            # clear ("") from a missing field.
            avatar_changed = ext is not None and "avatar_url" in ext
            if sync_to_bcn and (
                bot_name is not None or bot_desc is not None or avatar_changed
            ):
                sync_kwargs = {
                    "bot_id": bot_id,
                    "owner_id": user_id,
                    "bot_name": bot_name,
                    "bot_desc": bot_desc,
                }
                if request_headers:
                    sync_kwargs["request_headers"] = request_headers
                self._sync_bot_to_bcn(**sync_kwargs)

            # Fetch binding info from ac_entity_device_binding if exists
            binding_id = bot.get("binding_id")
            if binding_id:
                try:
                    service = self._device_service_provider()
                    binding = service.get_device(binding_id=binding_id)
                    if binding:
                        bot["device_binding"] = binding.to_dict()
                except Exception as e:
                    logger.warning(f"[bot_service.update_bot] Failed to get device binding {binding_id}: {e}")

            # Fetch template info if exists
            try:
                template = self._template_service.get_template(bot_id)
                if template:
                    bot["template_config"] = template.get("ext")
            except Exception as e:
                logger.warning(f"[bot_service.update_bot] Failed to get template for bot {bot_id}: {e}", exc_info=True)

            return bot
        except BotNotFoundError:
            raise
        except Exception as e:
            logger.error(f"[bot_service.update_bot] Failed to update bot {bot_id}: {e}")
            raise BotServiceError(f"Failed to update bot: {e}")

    def _maybe_refresh_codefuse_token_async(
        self,
        *,
        bot_id: str,
        user_id: str,
        old_template_config: Dict[str, Any],
        new_template_config: Dict[str, Any],
    ) -> None:
        """token 变化时异步把新 codefuse token 写入运行中容器。

        去重采用「密文比密文」同口径：``old_template_config`` 是落库前从 DB 抓回
        的旧密文（``enc:v1:`` 前缀）；新值在 ``update_template`` 落库后回读 DB
        拿到新密文。两者同为 DB 密文，比较才有意义。若直接拿「入参明文」与
        「DB 密文」比，恒不等，会导致每次带 token 的 PUT 都 exec 容器，违背去重
        初衷。只有真正变化才下发，下发在 daemon 线程里执行，失败只记 warning，
        不影响更新主流程（与现有 memory-init / cron 异步副作用语义一致）。
        """
        try:
            old_token_cipher = (old_template_config or {}).get("token")
            # 入参是否携带 token：未带则不下发（如仅改 yuque_kb_repos）。
            incoming_token = (new_template_config or {}).get("token")
            if not isinstance(incoming_token, str) or not incoming_token:
                return

            # 落库后回读 DB 拿新密文（update_template 已把入参明文加密落库），
            # 与旧 DB 密文同口径比较；回读失败则一律视为变化（安全侧触发刷新）。
            new_template_config_db = self._template_service.get_template_config(bot_id)
            new_token_cipher = (
                (new_template_config_db or {}).get("token")
                if isinstance(new_template_config_db, dict)
                else None
            )
            if (
                isinstance(old_token_cipher, str)
                and isinstance(new_token_cipher, str)
                and old_token_cipher == new_token_cipher
            ):
                return  # 密文一致 → 真未变化，跳过

            # 解密新 token 为明文 auth_code：用真实 vault，与 apply_device 启动
            # 解密、reconciler 重启解密同路径；空 master_key（singlebox）退化为
            # 透传，不另造空钥实例以免 enc:v1: 密文被静默写坏。
            plaintext = self._template_service.get_decrypted_codefuse_token(bot_id)
            if not plaintext:
                return
        except Exception as e:
            logger.warning(
                "[bot_service._maybe_refresh_codefuse_token_async] prepare failed for "
                "bot %s: %s", bot_id, e,
            )
            return

        env = get_current_env()
        thread = threading.Thread(
            target=bind_current_avernet_tenant(self._refresh_codefuse_token_on_device),
            kwargs={"bot_id": bot_id, "user_id": user_id, "plaintext_token": plaintext, "env": env},
            daemon=True,
            name=f"refresh-codefuse-token-{bot_id}",
        )
        thread.start()

    def write_codefuse_token_to_runtimes(
        self, *, bot: dict, plaintext_token: str, env: str,
    ) -> CodefuseWriteResult:
        """core 拥有的 "按 N 条 live runtime 写 codefuse token + 聚合" 领域策略。

        HTTP 同步路径（``aicoding/router.save_codefuse_token``）与异步刷新
        （``_refresh_codefuse_token_on_device``）都调这里，避免 adapter 内再造一份
        fan-out/exec 逻辑（arch.rules.md Rule 7）。baas 走 ``write_codefuse_token_baas``，
        arca/local 走 ``DeviceService.exec_shell``；单 runtime 失败隔离，聚合后由调用方
        翻译为 HTTPException / log。
        """
        from agentclaw.community.core.bot_management.codefuse_runtime_targets import (
            resolve_codefuse_runtime_binding_ids,
        )

        binding_ids = resolve_codefuse_runtime_binding_ids(
            bot=bot, publish_repo=self._bot_publish_repo,
            binding_repo=self._device_binding_repo, env=env,
        )
        result = CodefuseWriteResult(resolved_binding_ids=list(binding_ids))
        if not binding_ids:
            return result  # outcome=NO_BINDING

        baas_service = self._baas_service_provider()
        device_service = self._device_service_provider()
        for binding_id in binding_ids:
            binding = self._device_binding_repo.get_by_id(binding_id)
            if not binding:
                # 解到 id 但实体缺失：跳过，不记入 results（影响 outcome=NO_TARGET 判定）
                continue

            provider = getattr(binding, "device_provider", None) or ""
            if provider == "baas":
                bot_uuid = (getattr(binding, "device_props", None) or {}).get("bot_uuid")
                if not bot_uuid:
                    tr = CodefuseTargetResult(
                        getattr(binding, "id", None), provider, False, 400,
                        "Bot has no BaaS bot_uuid in device_props",
                    )
                else:
                    try:
                        from agentclaw.community.core.devices.services.baas_codefuse_writer import (
                            write_codefuse_token_baas,
                        )
                        write_codefuse_token_baas(baas_service, bot_uuid, plaintext_token)
                        tr = CodefuseTargetResult(
                            getattr(binding, "id", None), provider, True, 200, "",
                        )
                    except Exception as exc:
                        tr = CodefuseTargetResult(
                            getattr(binding, "id", None), provider, False, 502,
                            f"Failed to write token to container: {exc}",
                        )
            else:
                # arca / local：经 DeviceService.exec_shell 写 codefuse.json
                device_id = getattr(binding, "device_id", None)
                if not device_id:
                    tr = CodefuseTargetResult(
                        getattr(binding, "id", None), provider, False, 400,
                        "Device binding has no device_id",
                    )
                else:
                    try:
                        from agentclaw.community.core.bot_management.codefuse_token import (
                            build_codefuse_write_cmd_from_auth_code,
                        )
                        cmd = build_codefuse_write_cmd_from_auth_code(plaintext_token)
                        device_service.exec_shell(device_id=device_id, shell_cmd=cmd)
                        tr = CodefuseTargetResult(
                            getattr(binding, "id", None), provider, True, 200, "",
                        )
                    except Exception as exc:
                        tr = CodefuseTargetResult(
                            getattr(binding, "id", None), provider, False, 502,
                            f"Failed to write token to container: {exc}",
                        )

            result.results.append(tr)
            if tr.ok:
                result.wrote_any = True
            elif result.first_failure is None:
                result.first_failure = (tr.status_code, tr.detail)

        if result.wrote_any:
            result.outcome = CodefuseWriteOutcome.WROTE
        elif not result.attempted:
            result.outcome = CodefuseWriteOutcome.NO_TARGET
        else:
            result.outcome = CodefuseWriteOutcome.ALL_FAILED
        return result

    def _refresh_codefuse_token_on_device(
        self, *, bot_id: str, user_id: str, plaintext_token: str, env: str,
    ) -> None:
        """把明文 codefuse auth_code 写入 bot 所有在运行行时的 codefuse.json（异步 best-effort）。

        只负责 "找到 bot → 调用 core 编排 ``write_codefuse_token_to_runtimes``"，不再自造
        fan-out/exec 逻辑；单 runtime 失败已由 core 隔离，这里据 outcome 记日志。

        ``env`` 由调用方（``_maybe_refresh_codefuse_token_async`` 在请求边界）解析后透传，
        core 不在此处 ``get_current_env()`` 兜底（arch.rules Rule 14）。
        """
        try:
            bot = self._repository.get_by_id_and_owner(bot_id, user_id)
            if not bot:
                logger.warning(
                    "[bot_service._refresh_codefuse_token_on_device] bot not found: %s",
                    bot_id,
                )
                return

            result = self.write_codefuse_token_to_runtimes(
                bot=bot, plaintext_token=plaintext_token, env=env,
            )
            if result.wrote_any:
                logger.info(
                    "[bot_service._refresh_codefuse_token_on_device] codefuse.json refreshed: "
                    "bot_id=%s targets=%s", bot_id, result.attempted,
                )
            else:
                logger.warning(
                    "[bot_service._refresh_codefuse_token_on_device] codefuse.json not written "
                    "to any runtime: bot_id=%s outcome=%s", bot_id, result.outcome.value,
                )
        except Exception as e:
            logger.warning(
                "[bot_service._refresh_codefuse_token_on_device] failed for bot %s: %s",
                bot_id, e, exc_info=True,
            )

    def admin_update_bot(
        self,
        bot_id: str,
        owner_id: str,
        bot_name: Optional[str] = None,
        bot_desc: Optional[str] = None,
        template_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Admin update bot — operator can modify any bot's config.

        Unlike update_bot(), this method:
        - Does NOT require the caller to be the bot owner
        - Uses owner_id to locate the bot (not the caller's user_id)
        - Supports updating template_config (sandbox overrides)

        Args:
            bot_id: Bot ID
            owner_id: Bot owner's user ID (required for locating the bot)
            bot_name: New bot name (optional)
            bot_desc: New bot description (optional)
            template_config: Template configuration dict (optional, contains sandbox overrides)

        Returns:
            Dict with bot record, template_config, and optional warning

        Raises:
            BotNotFoundError: If bot not found
            BotServiceError: If update fails
        """
        if not owner_id:
            raise BotServiceError("owner_id is required for admin update")

        # Locate the bot by bot_id + owner_id
        bot = self._repository.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: {bot_id} (owner: {owner_id})")

        update_data = {}

        if bot_name is not None:
            if bot_name.strip():
                existing_bot = self._repository.get_by_bot_name(bot_name.strip())
                if existing_bot and existing_bot.get("bot_id") != bot_id:
                    raise BotNameExistsError(f"Bot name '{bot_name}' already exists")
            update_data["bot_name"] = bot_name

        if bot_desc is not None:
            update_data["bot_desc"] = bot_desc

        # Update ac_bots fields if any
        if update_data:
            update_data["modifier_id"] = "admin"
            bot = self._repository.update_by_owner(bot_id, owner_id, update_data)
            if not bot:
                raise BotNotFoundError(f"Bot not found after update: {bot_id}")

        # Update template_config if provided — merge with existing config
        if template_config is not None:
            # Validate sandbox fields if present (validate the merged result)
            from agentclaw.community.core.devices.services.sandbox_overrides import (
                SandboxOverrides,
                InvalidSandboxOverridesError,
            )

            # Merge: overlay the incoming fields on top of the existing config
            existing_config = self._template_service.get_template_config(bot_id) or {}
            merged_config = {**existing_config, **template_config}

            try:
                overrides = SandboxOverrides.from_template_config(merged_config)
                if not overrides.is_empty():
                    overrides.validate()
            except InvalidSandboxOverridesError as e:
                raise BotServiceError(f"沙箱配置校验失败: {e}") from e

            if self._template_service.exists_template(bot_id):
                self._template_service.update_template(
                    bot_id=bot_id,
                    template_config=merged_config,
                    template_type=bot.get("template_type"),
                    active_engine=bot.get("active_engine"),
                )
            else:
                self._template_service.create_template(
                    bot_id=bot_id,
                    template_config=merged_config,
                    template_type=bot.get("template_type"),
                    active_engine=bot.get("active_engine"),
                )
            logger.info("[bot_service.admin_update_bot] Template updated for bot %s by admin", bot_id)

        # Build response — return the full merged config
        result = self.get_bot(bot_id, owner_id)
        if template_config is not None:
            result["template_config"] = merged_config
        else:
            result["template_config"] = self._template_service.get_template_config(bot_id)

        # Add warning if sandbox config was changed
        if template_config is not None:
            result["warning"] = "沙箱配置变更将在下次重启后生效"

        return result

    def update_bot_ext(self, bot_id: str, user_id: str, ext_update: Dict[str, Any]) -> None:
        """局部更新 bot.ext 字段。

        Args:
            bot_id: Bot ID
            user_id: 用户 ID（用于权限检查）
            ext_update: 要更新的 ext 字段内容
        """
        reject_expert_origin_update(ext_update)
        bot = self.get_bot(bot_id, user_id)
        ext = bot.get("ext") or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except json.JSONDecodeError:
                ext = {}
        ext.update(ext_update)
        self._repository.update_by_owner(bot_id, user_id, {"ext": ext})
        logger.info(f"[bot_service.update_bot_ext] Updated ext for bot {bot_id}: {ext_update}")
