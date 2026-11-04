"""
Passport-token hot-update onto devices, service-bot instances and publish
bindings — moved verbatim from ``bot_service.py``.

Part of the mechanical split of ``bot_service.py``: ``BotCredentialsMixin`` members were
moved here verbatim and the facade composes them onto ``BotService``.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from agentclaw.community.core.devices.models import AllocatedDevice
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

from .common import (
    BotNotFoundError,
    BotServiceError,
    _CALLER_REFRESH_MAX_WORKERS,
    logger,
)


class BotCredentialsMixin:
    """Passport-token hot-update onto devices, service-bot instances and publish
    bindings."""

    def hot_update_passport_token_to_device(self, bot_id: str, user_id: str, token: str) -> dict:
        """刷新 Bot 的 Passport Token 并热更新到运行中的设备。

        流程：
        1. 获取 bot 并校验权限
        2. 校验 token 非空
        3. 根据 bot_type 路由到对应的热更新方法
           - service: 更新草稿态 + 已发布态（线上/预发）
           - personal: 更新单个 binding
        4. 返回更新结果

        Args:
            bot_id: Bot ID
            user_id: 用户 ID（权限校验）
            token: 调用方传入的最新 token

        Returns:
            包含新 token 摘要、设备更新结果的字典

        Raises:
            BotNotFoundError: Bot 不存在
            BotServiceError: Token 为空或设备热更新失败
        """
        logger.info(f"[bot_service.refresh_passport_token] Starting refresh for bot_id={bot_id}, user_id={user_id}")

        # 1. 获取 bot 并校验权限
        # 回调走 /api 前缀 → AvernetTenantMiddleware 套 DEFAULT 租户 teamclaw,
        # 但外部租户 bot 的 passport 刷新需跨租户直查。(bot_id, owner_workno)
        # 全局唯一,跨租户直查安全。
        bot = self._repository.get_by_id_and_owner(
            bot_id,
            user_id,
            execution_options={"skip_avernet_tenant_guard": True},
        )
        if not bot:
            logger.warning(f"[bot_service.refresh_passport_token] Bot not found: bot_id={bot_id}, user_id={user_id}")
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        bot_type = bot.get("bot_type", "personal")
        logger.info(f"[bot_service.refresh_passport_token] Bot type: {bot_type}, bot_id={bot_id}, user_id={user_id}")

        # 2. 使用调用方传入的 token
        if not token:
            logger.warning(f"[bot_service.refresh_passport_token] Token is empty for bot_id={bot_id}, user_id={user_id}, bot_type={bot_type}")
            raise BotServiceError("Passport Token 为空")

        new_token = token
        token_prefix = new_token[:20]
        logger.info(
            f"[bot_service.refresh_passport_token] Got new token for "
            f"bot_id={bot_id}, user_id={user_id}, bot_type={bot_type}, has_token=yes"
        )

        # 提取 agent_code
        from agentclaw.community.core.bot_management.utils import resolve_agent_code
        agent_code = resolve_agent_code(bot=bot, passport_plugin=self._passport_plugin)

        # 3. 热更新设备 header
        try:
            if bot_type == "service":
                logger.info(
                    f"[bot_service.refresh_passport_token] Routing to service bot: "
                    f"bot_id={bot_id}, user_id={user_id}"
                )
                from agentclaw.community.core.bot_management.addressing import stable_resource_owner_id
                resource_owner_id = stable_resource_owner_id(bot) if bot_id != "default" else user_id
                bindings = self._hot_update_service_bot_passport_token(
                    bot_id=bot_id, user_id=resource_owner_id, token=new_token, bot=bot,
                    agent_code=agent_code,
                )
            elif bot_type == "personal":
                logger.info(
                    f"[bot_service.refresh_passport_token] Routing to personal bot: "
                    f"bot_id={bot_id}, user_id={user_id}"
                )
                personal_result = self._hot_update_by_device_binding(
                    bot_id=bot_id, user_id=user_id, token=new_token,
                    binding_id=bot.get("binding_id"), agent_code=agent_code,
                )
                devices = personal_result.get("devices", [])
                bindings = [{
                    "binding_id": personal_result.get("binding_id"),
                    "type": "personal",
                    "device_count": len(devices),
                    "devices": devices,
                }]
            else:
                logger.warning(
                    f"[bot_service.refresh_passport_token] Skip passport token hot-update for "
                    f"unsupported bot_type={bot_type}: bot_id={bot_id}, user_id={user_id}"
                )
                return {
                    "token_prefix": token_prefix,
                    "bindings": [],
                    "skipped": True,
                    "reason": f"unsupported bot_type: {bot_type}",
                }
            logger.info(
                f"[bot_service.refresh_passport_token] Device hot-update succeeded: "
                f"bot_id={bot_id}, user_id={user_id}, bot_type={bot_type}, bindings={bindings}"
            )
        except BotServiceError:
            logger.warning(
                f"[bot_service.refresh_passport_token] Device hot-update failed: "
                f"bot_id={bot_id}, user_id={user_id}, bot_type={bot_type}"
            )
            raise
        except Exception as e:
            logger.error(
                f"[bot_service.refresh_passport_token] Device hot-update failed unexpectedly: "
                f"bot_id={bot_id}, user_id={user_id}, bot_type={bot_type}, error={e}"
            )
            raise BotServiceError(f"设备热更新失败: {e}") from e

        return {
            "token_prefix": token_prefix,
            "bindings": bindings,
        }

    def _hot_update_by_device_binding(
        self, bot_id: str, user_id: str, token: str, binding_id: int | None, agent_code: str = ""
    ) -> dict:
        """通过 device binding 热更新设备 header。

        适用于 personal bot 和 service bot 草稿态，直接通过 device_service
        获取 binding 并更新 header。

        流程：
        1. 校验 binding_id 是否存在
        2. 获取 device binding 记录
        3. 构造 AllocatedDevice
        4. 调用热更新接口更新设备 header
        """
        # ========== 步骤 1: 校验 binding_id 是否存在 ==========
        if not binding_id:
            logger.warning(
                f"[_hot_update_by_device_binding] Bot has no binding_id: "
                f"bot_id={bot_id}, user_id={user_id}"
            )
            raise BotServiceError("Bot 未绑定设备")

        # ========== 步骤 2: 获取 device binding 记录 ==========
        service = self._device_service_provider()
        binding = service.get_device(binding_id=binding_id)
        if not binding:
            logger.warning(
                f"[_hot_update_by_device_binding] Binding not found: "
                f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}"
            )
            raise BotServiceError("设备 binding 不存在")

        # ========== 步骤 3: 构造 AllocatedDevice ==========
        device = AllocatedDevice(
            device_id=binding.device_id,
            device_provider=binding.device_provider,
            device_props=binding.device_props,
        )

        # ========== 步骤 4: 调用热更新接口更新设备 header ==========
        update_result = service.update_device_headers(device=device, agent_pass_token=token, agent_code=agent_code)
        devices = update_result if isinstance(update_result, list) else []
        logger.info(
            f"[_hot_update_by_device_binding] Hot-update succeeded: "
            f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}, "
            f"device_id={binding.device_id}, updated_devices={len(devices)}"
        )
        return {"binding_id": binding_id, "devices": devices}

    def _hot_update_service_bot_passport_token(
        self, bot_id: str, user_id: str, token: str, bot: dict, agent_code: str = ""
    ) -> list[dict]:
        """Service Bot Passport Token 热更新入口。

        同时更新草稿态、已发布态和 ACTIVE caller 实例 binding（如果存在）。
        草稿态复用 `_hot_update_by_device_binding`，已发布态调用 `_hot_update_by_publish_binding`。
        caller 实例使用同一份 owner Passport token；caller 自己的
        ``x-caller-token`` 属于独立链路，不在这里处理。

        Args:
            bot_id: Bot ID
            user_id: 用户 ID
            token: 新 Passport Token
            bot: bot 字典（用于获取草稿态 binding_id）

        Returns:
            成功更新的 binding 列表，每项为
            {"binding_id": int, "type": "draft"|"online"|"verify"|"caller"}

        Raises:
            BotServiceError: 任一目标失败，或所有目标均不存在时抛出。所有
                已发现目标都会先 best-effort 尝试完毕，再聚合失败。
        """
        updated_bindings: list[dict] = []
        errors: list[str] = []

        # ========== 步骤 1: 更新草稿态 binding（和 personal bot 逻辑一致） ==========
        draft_binding_id = bot.get("binding_id")
        if draft_binding_id:
            logger.info(
                f"[_hot_update_service_bot_passport_token] Updating draft binding: "
                f"bot_id={bot_id}, user_id={user_id}, binding_id={draft_binding_id}"
            )
            try:
                draft_result = self._hot_update_by_device_binding(
                    bot_id=bot_id, user_id=user_id, token=token,
                    binding_id=draft_binding_id, agent_code=agent_code,
                )
                devices = draft_result.get("devices", [])
                updated_bindings.append({
                    "binding_id": draft_binding_id,
                    "type": "draft",
                    "device_count": len(devices),
                    "devices": devices,
                })
            except Exception as e:
                logger.warning(
                    f"[_hot_update_service_bot_passport_token] Draft binding update failed: "
                    f"bot_id={bot_id}, user_id={user_id}, binding_id={draft_binding_id}, error={e}"
                )
                errors.append(f"草稿态: {e}")
        else:
            logger.info(
                f"[_hot_update_service_bot_passport_token] No draft binding: "
                f"bot_id={bot_id}, user_id={user_id}"
            )

        # ========== 步骤 2: 更新已发布态 binding（线上 SUCCESS + 预发 VALIDATING） ==========
        env = get_current_env()
        publish_repo = self._bot_publish_repo

        _PUBLISHED_STATUS_BINDINGS = [
            (PublishStatus.SUCCESS.value, "online"),
            (PublishStatus.VALIDATING.value, "verify"),
        ]
        has_publish_record = False

        for status, binding_key in _PUBLISHED_STATUS_BINDINGS:
            try:
                publish_record = publish_repo.get_by_publish_bot_id(
                    publish_bot_id=bot_id, owner_id=user_id, env=env,
                    publish_status=status,
                )
            except Exception as e:
                logger.warning(
                    f"[_hot_update_service_bot_passport_token] {binding_key} publish query failed: "
                    f"bot_id={bot_id}, user_id={user_id}, status={status}, error={e}"
                )
                errors.append(f"{binding_key}: 发布记录查询失败: {e}")
                continue
            if not publish_record:
                continue
            has_publish_record = True

            binding_info = (publish_record.ext or {}).get("binding") or {}
            binding_id = binding_info.get(binding_key)
            if not binding_id:
                logger.error(
                    f"[_hot_update_service_bot_passport_token] Missing {binding_key} binding in {status} record: "
                    f"bot_id={bot_id}, user_id={user_id}, publish_id={publish_record.id}"
                )
                errors.append(f"{binding_key}: 发布记录中缺少 binding_id")
                continue

            logger.info(
                f"[_hot_update_service_bot_passport_token] Updating {binding_key} binding: "
                f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}"
            )
            try:
                publish_result = self._hot_update_by_publish_binding(
                    bot_id=bot_id, user_id=user_id, token=token,
                    binding_id=binding_id, agent_code=agent_code,
                )
                devices = publish_result.get("devices", [])
                updated_bindings.append({
                    "binding_id": binding_id,
                    "type": binding_key,
                    "device_count": len(devices),
                    "devices": devices,
                })
            except Exception as e:
                logger.warning(
                    f"[_hot_update_service_bot_passport_token] {binding_key} binding update failed: "
                    f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}, error={e}"
                )
                errors.append(f"{binding_key}: {e}")

        if not has_publish_record:
            logger.info(
                f"[_hot_update_service_bot_passport_token] No publish record: "
                f"bot_id={bot_id}, user_id={user_id}, env={env}"
            )

        # ========== 步骤 3: 并发更新 ACTIVE caller 实例 ==========
        try:
            caller_bindings = self._device_binding_repo.list_active_caller_instance_bindings(
                bot_id=bot_id,
                owner_id=user_id,
                env=env,
            )
        except Exception as e:
            logger.warning(
                f"[_hot_update_service_bot_passport_token] Caller binding query failed: "
                f"bot_id={bot_id}, user_id={user_id}, env={env}, error={e}"
            )
            errors.append(f"caller: binding 查询失败: {e}")
            caller_bindings = []

        if caller_bindings:
            logger.info(
                f"[_hot_update_service_bot_passport_token] Updating caller bindings: "
                f"bot_id={bot_id}, user_id={user_id}, caller_count={len(caller_bindings)}, "
                f"max_workers={_CALLER_REFRESH_MAX_WORKERS}"
            )
            update_caller = bind_current_avernet_tenant(
                self._hot_update_by_caller_binding
            )
            try:
                with ThreadPoolExecutor(
                    max_workers=_CALLER_REFRESH_MAX_WORKERS
                ) as executor:
                    future_to_binding = {
                        executor.submit(
                            update_caller,
                            bot_id=bot_id,
                            user_id=user_id,
                            token=token,
                            binding=binding,
                            agent_code=agent_code,
                        ): binding
                        for binding in caller_bindings
                    }
                    for future in as_completed(future_to_binding):
                        binding = future_to_binding[future]
                        try:
                            updated_bindings.append(future.result())
                        except Exception as e:
                            logger.warning(
                                f"[_hot_update_service_bot_passport_token] Caller binding update failed: "
                                f"bot_id={bot_id}, user_id={user_id}, binding_id={binding.id}, "
                                f"device_id={binding.device_id}, error={e}"
                            )
                            errors.append(
                                f"caller(binding_id={binding.id}): {e}"
                            )
            except Exception as e:
                logger.warning(
                    f"[_hot_update_service_bot_passport_token] Caller fan-out failed: "
                    f"bot_id={bot_id}, user_id={user_id}, error={e}"
                )
                errors.append(f"caller: 并发更新失败: {e}")
        else:
            logger.info(
                f"[_hot_update_service_bot_passport_token] No active caller binding: "
                f"bot_id={bot_id}, user_id={user_id}, env={env}"
            )

        if errors:
            error_detail = "; ".join(errors)
            raise BotServiceError(f"部分设备热更新失败: {error_detail}")

        if not updated_bindings:
            raise BotServiceError("服务 Bot 没有可用的设备 binding")

        logger.info(
            f"[_hot_update_service_bot_passport_token] Hot-update completed: "
            f"bot_id={bot_id}, user_id={user_id}, updated_bindings={updated_bindings}"
        )
        return updated_bindings

    def _hot_update_by_caller_binding(
        self,
        *,
        bot_id: str,
        user_id: str,
        token: str,
        binding: DeviceBindingRecord,
        agent_code: str = "",
    ) -> dict:
        """用 owner Passport token 更新一个 caller BaaS Bot 的 ACTIVE 设备。"""
        service = self._device_service_provider()
        device = AllocatedDevice(
            device_id=binding.device_id,
            device_provider=binding.device_provider,
            device_props={
                **(binding.device_props or {}),
                "bolt_id": bot_id,
                "entity_id": user_id,
            },
        )
        update_result = service.update_device_headers(
            device=device,
            agent_pass_token=token,
            agent_code=agent_code,
            active_only=True,
        )
        devices = update_result if isinstance(update_result, list) else []
        if not devices:
            raise BotServiceError("caller 实例没有 ACTIVE 物理设备")

        logger.info(
            f"[_hot_update_by_caller_binding] Hot-update succeeded: "
            f"bot_id={bot_id}, user_id={user_id}, binding_id={binding.id}, "
            f"device_id={binding.device_id}, updated_devices={len(devices)}"
        )
        return {
            "binding_id": binding.id,
            "type": "caller",
            "device_count": len(devices),
            "devices": devices,
        }

    def _hot_update_by_publish_binding(
        self, bot_id: str, user_id: str, token: str, binding_id: int, agent_code: str = ""
    ) -> dict:
        """通过 publish binding 热更新已发布 Service Bot 的设备 header。

        适用于 service bot 的已发布态（线上/预发）。
        只需通过 binding_id 查到 binding，取 device_id（即 BaaS bot_uuid），
        构造 AllocatedDevice 后批量更新即可。

        流程：
        1. 获取 device binding 记录
        2. 构造 AllocatedDevice
        3. 调用热更新接口
        """
        # ========== 步骤 1: 获取 device binding 记录 ==========
        binding = self._bot_publish_provider().get_device_binding_by_id(binding_id)
        if not binding or not binding.device_id:
            logger.warning(
                f"[_hot_update_by_publish_binding] Binding not found or invalid: "
                f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}"
            )
            raise BotServiceError("设备 binding 不存在或无效")

        # ========== 步骤 2: 构造 AllocatedDevice ==========
        service = self._device_service_provider()
        device = AllocatedDevice(
            device_id=binding.device_id,
            device_provider=binding.device_provider,
            device_props={
                "bolt_id": bot_id,
                "entity_id": user_id,
            },
        )

        # ========== 步骤 3: 调用热更新接口 ==========
        update_result = service.update_device_headers(device=device, agent_pass_token=token, agent_code=agent_code)
        devices = update_result if isinstance(update_result, list) else []
        logger.info(
            f"[_hot_update_by_publish_binding] Hot-update succeeded: "
            f"bot_id={bot_id}, user_id={user_id}, binding_id={binding_id}, "
            f"device_id={binding.device_id}, updated_devices={len(devices)}"
        )
        return {"binding_id": binding_id, "devices": devices}
