"""
BCN sync, provider registration and the DRM switches (both DRM class constants)
— moved verbatim from ``bot_service.py``.

Part of the mechanical split of ``bot_service.py``: ``BotCollaborationMixin`` members were
moved here verbatim and the facade composes them onto ``BotService``.
"""

from typing import Any, Dict, Literal, Optional

from agentclaw.community.core.bot_management.bcn_identity import bcn_bot_reference
from agentclaw.community.core.bot_management.collaboration_repair.profile import (
    outbound_avatar_from_record,
)

from .common import (
    logger,
)


class BotCollaborationMixin:
    """BCN sync, provider registration and the DRM switches (``_CLAUDE_CODE_BCN_REGISTER_DRM_ID``
    and ``_NEW_BOT_NAS_DRM_ID``)."""

    def _sync_bot_to_bcn(
        self,
        bot_id: str,
        owner_id: str,
        bot_name: Optional[str] = None,
        bot_desc: Optional[str] = None,
        request_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Sync bot name, summary and avatar projection to BCN.

        服务 Bot 使用稳定 entity 后缀；个人/桌面 Bot 保留 owner 后缀，例如 "20260421_gfdsz5vi:85020"。

        头像投影取自已保存记录（resolver 重新读取），不使用本次请求的
        partial ext —— 请求未携带的 ext 字段不能覆盖已持久化数据。投影
        读取失败（字段类型非法 / ext JSON 损坏）则省略 ``avatar_url`` 字段，
        BCN 保留远端旧值；绝不伪造空串把读不出来的数据变成"显式清空"。

        Args:
            bot_id: TC 页面上的 bot_id
            owner_id: 当前 Owner 或历史 entity locator
            bot_name: 新的 Bot 名称（如果更新了）
            bot_desc: 新的 Bot 简介（如果更新了）
            request_headers: 原始 HTTP 请求头，BCN 层会筛选需要透传的认证头
        """
        try:
            from agentclaw.community.core.bot_management.services.bcn_service import BcnServiceError

            # 获取当前 bot 信息，用于填充未更新的字段
            from agentclaw.community.core.bot_management.addressing import BotAddressResolver

            bot = BotAddressResolver(self._repository).resolve(
                bot_id=bot_id, legacy_owner_id=owner_id
            ).bot
            bcn_bot_id = bcn_bot_reference(bot)

            # 使用传入的新值或现有的值
            final_name = bot_name if bot_name is not None else bot.get("bot_name", bot_id)
            final_summary = bot_desc if bot_desc is not None else bot.get("bot_desc", "")

            onboard_kwargs = {
                "bot_id": bcn_bot_id,
                "name": final_name or bot_id,
                "summary": final_summary or "",
            }
            if request_headers:
                onboard_kwargs["request_headers"] = request_headers
            avatar_url = outbound_avatar_from_record(
                bot, log_context="[bot_service._sync_bot_to_bcn]"
            )
            if avatar_url is not None:
                onboard_kwargs["avatar_url"] = avatar_url
            result: Dict[str, Any] = self._bcn_service.onboard_bot(**onboard_kwargs)

            if result.get("skipped") is True:
                logger.warning(
                    f"[bot_service._sync_bot_to_bcn] BCN sync skipped: "
                    f"bot_id={bcn_bot_id}, name={final_name}"
                )
            elif result.get("onboarded") is not True:
                # Project only documented onboard-response fields, matching the
                # register path's log convention (raw payloads stay private).
                logger.warning(
                    f"[bot_service._sync_bot_to_bcn] BCN did not confirm onboarding: "
                    f"bot_id={bcn_bot_id}, "
                    f"bot_uuid={result.get('bot_uuid')!r}, "
                    f"onboarded={result.get('onboarded')!r}, "
                    f"message={result.get('message')!r}"
                )
            else:
                logger.info(
                    f"[bot_service._sync_bot_to_bcn] Synced to BCN: "
                    f"bot_id={bcn_bot_id}, name={final_name}"
                )

        except BcnServiceError as e:
            # BCN 同步失败不影响主流程，只记录警告日志
            logger.warning(
                f"[bot_service._sync_bot_to_bcn] Failed to sync to BCN: "
                f"bot_id={bot_id}, owner_id={owner_id}, error={e}"
            )
        except Exception as e:
            logger.warning(
                f"[bot_service._sync_bot_to_bcn] Unexpected error syncing to BCN: "
                f"bot_id={bot_id}, owner_id={owner_id}, error={e}"
            )

    @staticmethod
    def _resolve_bcn_provider_connection_mode(
        active_engine: Optional[str],
        bot_type: Optional[str],
    ) -> Optional[Literal["plugin"]]:
        """Return the BCN connection mode selected by the Bot lifecycle."""
        if active_engine == "openclaw" and bot_type in ("personal", "desktop"):
            return "plugin"
        return None

    def _register_bot_to_bcn_as_provider(
        self,
        bot_id: str,
        user_id: str,
        owner_workno: str,
        bot_name: str,
        bot_summary: str,
        connection_mode: Optional[Literal["plugin"]] = None,
        *,
        respect_drm: bool = True,
        provider_bot_ref: str | None = None,
        avatar_url: str | None = None,
    ) -> None:
        """Register with BCN best effort.

        Create/start/restart pass ``respect_drm=False`` so every legal lifecycle
        execution attempts registration. Environment credential checks remain
        owned by BcnService. Success only persists ``bcn_registered=True``.

        ``avatar_url`` 是可省略的边界参数：``None`` 不发送该字段（BCN 保留
        旧值），``""`` 显式清空远端头像。实际 Backend 调用方必须始终传调用点
        已持有记录的当前投影（``outbound_avatar_from_record``）—— 投影读不
        出来时以 ``None`` 省略，而不是伪造空串。
        """
        if respect_drm and not self._is_claude_code_bcn_register_enabled():
            logger.info(
                f"[bot_service._register_bot_to_bcn_as_provider] disabled by DRM, "
                f"skip bot_id={bot_id} owner={owner_workno} connection_mode={connection_mode}"
            )
            return

        try:
            from agentclaw.community.core.bot_management.services.bcn_service import BcnServiceError

            register_kwargs: Dict[str, Any] = {
                "teamclaw_bot_uuid": bot_id,
                "owner_workno": owner_workno,
                "name": bot_name,
                "summary": bot_summary,
            }
            if provider_bot_ref is not None:
                register_kwargs["provider_bot_ref"] = provider_bot_ref
            if connection_mode is not None:
                register_kwargs["connection_mode"] = connection_mode
            if avatar_url is not None:
                register_kwargs["avatar_url"] = avatar_url
            result = self._bcn_service.register_provider_bot(
                **register_kwargs,
            )

            if result.get("skipped"):
                logger.info(
                    f"[bot_service._register_bot_to_bcn_as_provider] env-skipped, "
                    f"bot_id={bot_id} owner={owner_workno} connection_mode={connection_mode}"
                )
                return

            # bot_runtime_token 暂不落库 (按需求方要求), 仅在 ext 标记一个布尔
            # 成功标志 ``bcn_registered=True`` 用于上层判断 / 排查; 包含幂等成功.
            # TODO: 后续如确认下行调用需要 token, 再补持久化方式 (ext / secret store).
            bot_uuid = result.get("bot_uuid") or ""
            idempotent = result.get("idempotent_replay", False)
            has_token = bool(result.get("bot_runtime_token"))
            logger.info(
                f"[bot_service._register_bot_to_bcn_as_provider] registered "
                f"bot_id={bot_id} owner={owner_workno} "
                f"bot_uuid={bot_uuid} idempotent={idempotent} "
                f"runtime_token_present={has_token} connection_mode={connection_mode}"
            )

            try:
                self.update_bot_ext(
                    bot_id=bot_id,
                    user_id=user_id,
                    ext_update={"bcn_registered": True},
                )
            except Exception as ext_err:
                logger.warning(
                    f"[bot_service._register_bot_to_bcn_as_provider] "
                    f"failed to mark bcn_registered in bot.ext: {ext_err}"
                )

        except BcnServiceError as e:
            logger.warning(
                f"[bot_service._register_bot_to_bcn_as_provider] BCN register failed: "
                f"bot_id={bot_id} owner={owner_workno} connection_mode={connection_mode} "
                f"error_type={type(e).__name__}"
            )
        except Exception as e:
            logger.warning(
                f"[bot_service._register_bot_to_bcn_as_provider] Unexpected error: "
                f"bot_id={bot_id} owner={owner_workno} connection_mode={connection_mode} "
                f"error_type={type(e).__name__}"
            )

    # DRM 控制开关: claude_code 启动时是否往 BCN 注册 Provider bot.
    # value 取 "true"/"on"/"1" 视为开启; 空 / 异常 / 未配置默认开启.
    # 排查日志关键字: [DRM] ClaudeCodeBcnRegister
    _CLAUDE_CODE_BCN_REGISTER_DRM_ID = (
        "Alipay.agentclaw:name=com.alipay.agentclaw.service.drm."
        "ClaudeCodeBcnRegister.enabled,version=3.0@DRM"
    )

    # DRM 控制开关: 新建 bot 是否使用 NAS 存储 (value=="nas" 才走 NAS, 否则 OSS).
    _NEW_BOT_NAS_DRM_ID = (
        "Alipay.agentclaw:name=com.alipay.agentclaw.service.drm."
        "NewBotUserNas.use_nas,version=3.0@DRM"
    )

    def _is_claude_code_bcn_register_enabled(self) -> bool:
        """读 DRM 判断 claude_code BCN 注册是否启用. 默认开 (失败也开).

        Returns:
            True: DRM 显式开启，或 DRM 未配置 / 不可用
            False: DRM 显式配置为关闭值
        """
        try:
            raw_value = self._drm_reader.read(self._CLAUDE_CODE_BCN_REGISTER_DRM_ID)
        except Exception as error:
            logger.warning(
                "[DRM] ClaudeCodeBcnRegister unavailable; default enabled "
                "error_type=%s",
                type(error).__name__,
            )
            return True

        value = str(raw_value).strip().lower() if raw_value is not None else ""
        if not value:
            logger.info("[DRM] ClaudeCodeBcnRegister unset; default enabled")
            return True

        enabled = value in ("true", "on", "1")
        logger.info(
            "[DRM] ClaudeCodeBcnRegister explicit_value_present=true enabled=%s",
            enabled,
        )
        return enabled

    def _is_new_bot_use_nas(self) -> bool:
        """读 DRM 判断新建 bot 是否使用 NAS 存储. 默认 OSS (空/失败也 OSS).

        Returns:
            True: DRM 值为 "nas"，新建 bot 走 NAS 分支
            False: 其他情况（空、"oss"、取不到等），走 OSS 分支
        """
        value = self._drm_reader.read(self._NEW_BOT_NAS_DRM_ID)
        logger.info(f"[DRM] NewBotUserNas value={value}")
        return value == "nas"
