"""
IP/user lookup plus quota, preflight and naming-rule checks — moved verbatim
from ``bot_service.py``.

Part of the mechanical split of ``bot_service.py``: ``BotQuotaMixin`` members were
moved here verbatim and the facade composes them onto ``BotService``.
"""

from typing import Any, Dict, Optional, TYPE_CHECKING

from agentclaw.community.core.bot_management.bot_quota import BotQuotaUnavailableError
from agentclaw.community.core.devices.models import DeviceAllocationMode, DeviceBindingStatus
from agentclaw.community.utils.env_utils import get_current_env

from .common import (
    BotLimitExceededError,
    BotNameExistsError,
    DefaultBotTeclawNotAllowedError,
    DeviceLimitError,
    logger,
)

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.bot_quota_service_protocol import (
        BotQuotaServiceProtocol,
    )


class BotQuotaMixin:
    """IP/user lookup plus quota, preflight and naming-rule checks."""

    async def get_bot_by_ip_and_user(self, ip: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        根据 IP 地址和 User ID 查询对应的 Bot 记录。

        查询逻辑：
        1. 查询 ac_entity_device_binding 表，entity_id=user_id, status=ACTIVE
        2. 匹配 device_props.device_locator 中的 IP 地址（如 "11.68.29.96:22"）
        3. 根据 binding_id 查询对应的 bot 记录

        Args:
            ip: IP 地址（如 "11.68.29.96"）
            user_id: 用户 ID

        Returns:
            Bot 记录字典，未找到返回 None
        """
        try:
            # Step 1: Query ACTIVE device bindings for the user (paginated)
            device_binding_repo = self._get_device_binding_repo()
            page = 1
            page_size = 100
            matching_binding = None

            while True:
                total, bindings = device_binding_repo.list_bindings(
                    entity_id=user_id,
                    entity_type="staff",
                    status="ACTIVE",
                    env=get_current_env(),
                    page=page,
                    page_size=page_size
                )

                if not bindings:
                    break

                # Step 2: Search for binding record matching the IP address in current page
                for binding in bindings:
                    device_props = binding.device_props or {}
                    device_locator = device_props.get("device_locator", "")

                    # device_locator format: "11.68.29.96:22" or just "11.68.29.96"
                    if device_locator:
                        # Extract IP part (remove port if present)
                        locator_ip = device_locator.split(":")[0] if ":" in device_locator else device_locator
                        if locator_ip == ip:
                            matching_binding = binding
                            logger.info(f"[get_bot_by_ip_and_user] Found matching binding: id={binding.id}, device_locator={device_locator}")
                            break

                if matching_binding:
                    break

                # Check if there are more pages
                if len(bindings) < page_size:
                    break
                page += 1

            if not matching_binding:
                logger.info(f"[get_bot_by_ip_and_user] No ACTIVE device bindings found for user {user_id} with IP {ip}")
                return None

            # Step 3: Query bot by binding_id
            bot = self._repository.get_by_binding_id(matching_binding.id)

            if bot:
                logger.info(f"[get_bot_by_ip_and_user] Found bot: bot_id={bot.get('bot_id')}, binding_id={matching_binding.id}")
            else:
                logger.info(f"[get_bot_by_ip_and_user] No bot found for binding_id={matching_binding.id}")

            return bot

        except Exception as e:
            logger.error(f"[get_bot_by_ip_and_user] Error querying bot by IP {ip} and user {user_id}: {e}")
            return None

    def is_first_bot(self, user_id: str) -> bool:
        """First bot iff the owner has zero bots (current env; tenant enforced by guard)."""
        return self._repository.count_by_owner(user_id) == 0

    def is_first_personal_bot(self, user_id: str) -> bool:
        """Return whether the owner has no live personal Bot in the current scope."""
        return not self._repository.exists_by_owner_and_bot_type(
            user_id, "personal"
        )

    def _check_bot_count_limit(self, owner_id: str) -> None:
        """Enforce the per-owner bot count limit.

        上限优先级：``ac_access_control_policy.policy.bots_ceiling`` > 0
        → ``device_allocation.max_devices_per_entity`` (default 5)。

        桌面 Bot（bot_type="desktop"）运行在用户本地 VM，不占用云端容器资源，
        因此不计入数量限制。TeClaw Bot 同样不占用此额度。

        Args:
            owner_id: 当前操作用户

        Raises:
            BotLimitExceededError: 已达到该用户允许的最大 Bot 数量
        """
        # 1. 尝试从 policy 表读取用户专属上限
        max_bots = self.get_bots_ceiling_for_owner(owner_id)

        if max_bots <= 0:
            return
        try:
            current_raw = self._repository.count_by_owner(
                owner_id, exclude_bot_type="desktop", exclude_active_engine="teclaw"
            )
            current = int(current_raw)
        except (TypeError, ValueError):
            return
        except Exception as e:
            logger.warning(
                f"[bot_service._check_bot_count_limit] count_by_owner failed for {owner_id}: {e}"
            )
            return
        logger.info(
            f"[bot_service._check_bot_count_limit] owner={owner_id} "
            f"cloud_bot_count={current} max={max_bots}"
        )
        if current >= max_bots:
            raise BotLimitExceededError(
                f"已达到 Bot 数量上限 ({max_bots})，当前 {current} 个。请删除部分 Bot 后再创建新的。"
            )

    def get_bots_ceiling_for_owner(self, owner_id: str) -> int:
        """获取用户的 BOT 数量上限，优先从 policy 表读取。

        Public because it is the single definition of the ceiling: creation
        enforces it here, and a surface that *reports* the quota must resolve it
        the same way. Reading ``PolicyService.get_bots_ceiling`` directly instead
        picks up that method's own hardcoded default (5) rather than the
        configured allocation limit, so the advertised and enforced ceilings can
        disagree whenever ``max_devices_per_entity`` is not 5.

        Priority:
        1. PolicyService.get_bots_ceiling (per-user, from DB)
        2. DeviceAllocationConfig.max_devices_per_entity (global config)
        3. 0 (if config is invalid — disables the check)
        """
        config_default = 0
        try:
            config_default = int(self._allocation_config.max_devices_per_entity)
        except Exception:
            pass

        if self._policy_service is None:
            return config_default

        try:
            return self._policy_service.get_bots_ceiling(
                entity_id=owner_id, default=config_default,
            )
        except Exception as e:
            logger.warning(
                "[bot_service.get_bots_ceiling_for_owner] "
                "policy_service.get_bots_ceiling failed for %s: %s",
                owner_id, e,
            )
            return config_default

    def check_create_bot_preflight(
        self,
        user_id: str,
        bot_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        bot_name: Optional[str] = None,
        space_id: int | None = None,
        space_quota: bool = False,
    ) -> None:
        """Validate whether a bot creation request can start external auth.

        The create flow may be two-phase: Passport auth is requested first, and
        the bot is persisted later from /auth-status. Quota checks that can be
        evaluated before Passport should run here so users are not sent through
        authorization only to fail during the second phase.

        ``bot_name`` (when known) is checked for uniqueness here for the same
        reason: ``create_bot`` rejects a duplicate, but only *after* the Passport
        application has already happened, leaving an identity behind with no bot
        and repeating that external side effect on every retry. The service-level
        check stays where it is — this one narrows the window, it does not close
        it, since another create can take the name in between.
        """
        # TeClaw does not consume Bot quota; other validations still apply.
        if (engine_type or "").strip().lower() != "teclaw":
            if space_quota:
                self._require_bot_quota_service().assert_can_add(
                    owner_id=user_id, space_id=space_id
                )
            else:
                self._check_bot_count_limit(user_id)
        if bot_name and bot_name.strip():
            if self._repository.get_by_bot_name(bot_name.strip()):
                raise BotNameExistsError(f"Bot name '{bot_name}' already exists")
        if bot_id is not None and engine_type is not None:
            self._validate_default_bot_engine(bot_id, engine_type)

    def _validate_default_bot_engine(self, bot_id: str, engine_type: str) -> None:
        """Reject Teclaw Cloud as the engine of the reserved Default Bot."""
        if bot_id == "default" and self.is_teclaw_bot(engine_type):
            raise DefaultBotTeclawNotAllowedError()

    def _require_bot_quota_service(self) -> "BotQuotaServiceProtocol":
        service = self._bot_quota_service
        if service is None:
            raise BotQuotaUnavailableError("Bot quota service is not configured")
        return service

    def _check_device_limit(self, entity_id: str, entity_type: str, owner_id: str) -> None:
        """
        Check if device limit is reached for the entity based on user's bots.

        Counts devices that are actively bound to user's bots (ACTIVE/PENDING/FAILED),
        regardless of whether the bot is ACTIVE, PENDING, or FAILED.

        上限来源为 per-user ceiling（``get_bots_ceiling_for_owner``，优先读
        ``ac_access_control_policy.policy.bots_ceiling``，fallback 到
        ``device_allocation.max_devices_per_entity``），与 ``_check_bot_count_limit``
        同源，确保动态上限对绑定了 device 的 bot 同样生效。

        Args:
            entity_id: Entity ID
            entity_type: Entity type
            owner_id: Owner user ID (to find user's bots)

        Raises:
            DeviceLimitError: If device limit is reached
        """
        try:
            mode_str = self._allocation_config.mode
            max_devices = self.get_bots_ceiling_for_owner(owner_id)

            # ceiling 无效（<=0）时放行，与 _check_bot_count_limit 语义一致，
            # 避免 active_device_count >= 0 恒真导致第一个 bot 就被拦。
            if max_devices <= 0:
                return

            try:
                mode = DeviceAllocationMode(mode_str)
            except ValueError:
                mode = DeviceAllocationMode.MULTI

            # Only check limit for multi-device mode
            if mode != DeviceAllocationMode.MULTI:
                return

            # Get all bots for this owner
            total_bots, bots = self._repository.list_by_owner(
                owner_id=owner_id,
                page=1,
                page_size=1000,  # Get all bots
            )

            # Collect binding_ids from bots that have devices
            # 桌面 Bot 运行在用户本地 VM，不占用云端容器资源，因此不计入 device 限制。
            binding_ids = []
            bot_binding_map = {}  # binding_id -> bot info for logging
            skipped_desktop = 0
            for bot in bots:
                if (bot.get("active_engine") or "").strip().lower() == "teclaw":
                    continue
                if bot.get("bot_type") == "desktop":
                    skipped_desktop += 1
                    continue
                binding_id = bot.get("binding_id")
                if binding_id:
                    binding_ids.append(binding_id)
                    bot_binding_map[binding_id] = {
                        "bot_id": bot.get("bot_id"),
                        "bot_status": bot.get("status"),
                    }
            if skipped_desktop > 0:
                logger.info(
                    f"[bot_service._check_device_limit] owner={owner_id} "
                    f"skipped {skipped_desktop} desktop bot(s) from device limit check"
                )

            if not binding_ids:
                logger.info(f"[bot_service._check_device_limit] No bound devices for owner {owner_id}, count=0/{max_devices}")
                return

            # Query device binding status for these binding_ids
            # Use device service to get binding details
            active_device_count = 0
            service = self._device_service_provider()

            for binding_id in binding_ids:
                try:
                    binding = service.get_device(binding_id=binding_id)
                    if binding and binding.status in [
                        DeviceBindingStatus.ACTIVE.value,
                        DeviceBindingStatus.PENDING.value,
                        DeviceBindingStatus.FAILED.value,
                        DeviceBindingStatus.STOPPED.value,
                    ]:
                        active_device_count += 1
                        bot_info = bot_binding_map.get(binding_id, {})
                        logger.debug(
                            f"[bot_service._check_device_limit] Counting device: "
                            f"binding_id={binding_id}, device_status={binding.status}, "
                            f"bot_id={bot_info.get('bot_id')}, bot_status={bot_info.get('bot_status')}"
                        )
                except Exception as e:
                    # If can't get device info, assume it counts (conservative)
                    logger.warning(f"[bot_service._check_device_limit] Failed to get device {binding_id}: {e}")
                    active_device_count += 1

            if active_device_count >= max_devices:
                raise DeviceLimitError(
                    f"已达到设备数量上限 ({max_devices})，当前关联设备数: {active_device_count}。"
                    f"请释放部分Bot后再创建新的Bot。"
                )

            logger.info(
                f"[bot_service._check_device_limit] Device count {active_device_count}/{max_devices} "
                f"for owner {owner_id} (entity {entity_type}/{entity_id})"
            )

        except DeviceLimitError:
            raise
        except Exception as e:
            logger.warning(f"[bot_service._check_device_limit] Failed to check device limit: {e}")
            # Don't block bot creation if check fails

    def _resolve_bot_name(
        self,
        bot_name: Optional[str],
        bot_id: str,
        user_id: str,
        nick_name: str
    ) -> str:
        """
        Resolve bot name according to the naming rules:
        - First bot (user has no existing bots): use nick_name (花名)
        - Non-first bot: if bot_name not provided, use bot_id

        Args:
            bot_name: User-provided bot name (optional)
            bot_id: Generated bot ID
            user_id: User ID
            nick_name: User nickname (花名)

        Returns:
            Resolved bot name
        """
        # If bot_name is explicitly provided, use it
        if bot_name is not None and bot_name.strip():
            return bot_name.strip()

        # Check if this is the first bot for the user
        is_first = self.is_first_bot(user_id)

        if is_first:
            # First bot: use nick_name (花名) as default
            resolved_name = nick_name.strip() if nick_name and nick_name.strip() else str(bot_id)
            logger.info(f"[bot_service._resolve_bot_name] First bot for user {user_id}, using nick_name: {resolved_name}")
            return resolved_name
        else:
            # Non-first bot: use bot_id as default
            logger.info(f"[bot_service._resolve_bot_name] Non-first bot for user {user_id}, using bot_id: {bot_id}")
            return str(bot_id)
