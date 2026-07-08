"""
Channel Service
Business logic for channel configuration management.
"""
import asyncio
import copy
import json
from dataclasses import dataclass
from typing import Optional

from injector import inject

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotService,
)
from agentclaw.community.core.channel.json_config_utils import JsonConfigFile
from agentclaw.community.core.channel.services.repositories import ChannelRepository, ChannelRecord
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.di.modules.skill_center_module import (
    DeviceFilesystemDispatcher,
)
from agentclaw.community.core.devices.services.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_sync import (
    DeviceSyncUnavailableError,
)

logger = get_logger()

# Engine token for teclaw bots (``ac_bots.active_engine``). Channels for a teclaw
# bot are delivered by recomposing + pushing the whole artifact (the channel rides
# in the composed ``engine_overrides``), not by the direct ``openclaw.json`` write.
TECLAW_ENGINE = "teclaw"


class ChannelService:
    """Channel business logic service."""

    @inject
    def __init__(
        self,
        repository: ChannelRepository,
        resolver: DeviceContextResolver,
        device_fs_dispatcher: DeviceFilesystemDispatcher,
        bot_service: BotService,
        device_sync_dispatcher: DeviceSyncDispatcher,
    ) -> None:
        self._repository = repository
        self._resolver = resolver
        self._device_fs_dispatcher = device_fs_dispatcher
        self._bot_service = bot_service
        self._device_sync_dispatcher = device_sync_dispatcher

    def list_channels(
        self,
        *,
        type: str,
        identity_id: str,
        bind_bot_id: str,
    ) -> list[ChannelRecord]:
        """
        查询渠道列表
        - 根据 type + identity_id + aideskdingding(默认配置) + bind_bot_id 查询
        """
        # identity_ids 包含传入的 identity_id 和 aideskdingding
        identity_ids = [identity_id, "aideskdingding"]
        return self._repository.get_by_type_and_identity_ids(
            type=type,
            identity_ids=identity_ids,
            bind_bot_id=bind_bot_id,
        )

    def create_channel(
        self,
        *,
        type: str,
        description: Optional[str],
        identity_id: str,
        bind_bot_id: str,
        config: dict,
        status: str = "0",
        stage: Optional[str] = None,
    ) -> int:
        """创建渠道配置"""
        return self._repository.insert_channel(
            type=type,
            description=description,
            identity_id=identity_id,
            bind_bot_id=bind_bot_id,
            config=config,
            status=status,
            stage=stage,
        )

    def update_status(self, channel_id: int, status: str) -> None:
        """更新渠道状态
        - status="1": 生效
        - status="0": 失效
        """
        self._repository.update_status_by_id(
            channel_id=channel_id,
            status=status,
        )

    def delete(self, channel_id: int) -> None:
        self._repository.delete_by_id(channel_id=channel_id)

    def update_channel(
        self,
        *,
        channel_id: int,
        type: str,
        description: Optional[str],
        identity_id: str,
        bind_bot_id: str,
        config: dict,
        status: str,
        stage: Optional[str] = None,
    ) -> None:
        """根据 id 更新配置"""
        self._repository.update_by_id(
            channel_id=channel_id,
            type=type,
            description=description,
            identity_id=identity_id,
            bind_bot_id=bind_bot_id,
            config=config,
            status=status,
            stage=stage,
        )

    def get_channel_by_id(self, channel_id: int) -> Optional[ChannelRecord]:
        """根据 id 查询渠道详情"""
        return self._repository.get_by_id(channel_id)

    # ── provider dispatch (teclaw vs openclaw) ──────────────────────────────
    def _is_teclaw_bot(self, bot_id: str, user_id: str) -> bool:
        """Whether ``bot_id`` runs on the teclaw engine.

        Resolved from ``ac_bots.active_engine`` via :meth:`BotService.get_bot`
        (``user_id`` is the channel row's ``identity_id`` — the same id the
        openclaw write path keys on at ``get_bot_work_path`` below). A
        missing/unknown bot is treated as non-teclaw so the established openclaw
        file-write path runs unchanged.

        Known limitation: ``get_bot`` is owner-scoped, so this only resolves
        correctly when the channel's ``identity_id`` is the bot owner — true for
        user-configured channels (``create_channel`` stores the acting user). A
        shared default row (``identity_id == "aideskdingding"``) resolves to
        non-teclaw and would fall to the openclaw path; that path already keys on
        ``identity_id`` the same way, and there is no owner-free bot lookup today,
        so this matches existing behavior rather than introducing a new gap.
        """
        try:
            bot = self._bot_service.get_bot(bot_id, user_id)
        except BotNotFoundError:
            return False
        return bot.get("active_engine") == TECLAW_ENGINE

    async def _deliver_teclaw_channel(self, channel: ChannelRecord) -> None:
        """Recompose + deliver the whole artifact for a teclaw bot (best-effort).

        The channel change is already persisted, so the recompose (via the
        device-sync seam, same path MCP/file edits use) picks it up through the
        composed ``engine_overrides``. Teclaw delivery never raises — the plugin
        catches transport/compose errors and returns a result dict — so a delivery
        miss does not fail the channel write (DB stays the source of truth).
        """
        try:
            ctx = self._resolver.resolve_for_bot(
                channel.bind_bot_id, channel.identity_id,
            )
            plugin = self._device_sync_dispatcher.dispatch(ctx)
        except DeviceSyncUnavailableError as e:
            logger.warning(
                "[ChannelService] teclaw delivery skipped for bot=%s: %s",
                channel.bind_bot_id, e,
            )
            return
        # sync_symlinks([]) → TeclawDeviceSyncPlugin recomposes + POSTs the
        # artifact; the list arg is ignored (whole-artifact delivery). Run off the
        # event loop — the plugin's transport is synchronous httpx. The teclaw
        # plugin already catches transport/compose errors and returns a result
        # dict, but we guard defensively so a misbehaving provider can never turn a
        # best-effort delivery into a failed channel write (DB is the source of
        # truth; the next compose reconciles).
        try:
            await asyncio.to_thread(plugin.sync_symlinks, [])
        except Exception as e:  # noqa: BLE001 — best-effort delivery
            logger.warning(
                "[ChannelService] teclaw delivery failed for bot=%s: %s",
                channel.bind_bot_id, e,
            )

    async def _dispatch_channel_sync(self, channel: ChannelRecord, *, action: str) -> None:
        """Route a channel sync to the bot's container path.

        teclaw → recompose + deliver (best-effort); everything else → the existing
        direct ``openclaw.json`` write (may raise; preserved verbatim).
        """
        if self._is_teclaw_bot(channel.bind_bot_id, channel.identity_id):
            await self._deliver_teclaw_channel(channel)
        else:
            await self.sync_channel_to_openclaw(channel.id, action=action)

    async def set_channel_status(self, channel_id: int, status: str) -> None:
        """Enable/disable a channel and reflect it on the running bot.

        Ordering is provider-dependent, and intentionally so:

        * **teclaw** — persist the status **first**, then deliver (best-effort).
          The teclaw runtime is updated by recomposing the whole artifact, which
          reads the freshly-persisted status from the DB, so the write must
          precede delivery. Delivery never raises, so a transient delivery miss
          leaves the DB (source of truth) correct and is reconciled on the next
          compose.
        * **openclaw** — write ``openclaw.json`` **first** (may raise), then
          persist. This preserves today's fail-closed behavior: if the file write
          fails the status is not persisted and the endpoint surfaces the error.
        """
        channel = self._repository.get_by_id(channel_id)
        if not channel:
            raise ValueError(f"Channel not found: {channel_id}")

        if self._is_teclaw_bot(channel.bind_bot_id, channel.identity_id):
            self.update_status(channel_id, status)
            await self._deliver_teclaw_channel(channel)
        else:
            action = "apply" if status == "1" else "remove"
            await self.sync_channel_to_openclaw(channel_id, action=action)
            self.update_status(channel_id, status)

    async def sync_active_channel(self, channel_id: int) -> None:
        """Re-deliver an already-active channel after its config changed.

        Used by the update path, where the new config is persisted **before** this
        call, so a teclaw recompose reads the fresh config. Dispatches teclaw
        (recompose+deliver, best-effort) vs openclaw (apply-write, may raise).
        """
        channel = self._repository.get_by_id(channel_id)
        if not channel:
            raise ValueError(f"Channel not found: {channel_id}")
        await self._dispatch_channel_sync(channel, action="apply")

    async def sync_channel_to_openclaw(self, channel_id: int, *, action: str) -> bool:
        """同步渠道配置到 openclaw 配置（添加/更新/删除）

        Args:
            channel_id: 渠道配置ID
            action: 操作类型，"apply" (生效) 或 "remove" (失效)

        Returns:
            True if successful, False otherwise

        Raises:
            FileNotFoundError: If openclaw.json does not exist
            ValueError: If action is invalid or channel not found
            BotNotFoundError: If bot not found
        """
        # 1. 根据 channel_id 查询配置
        channel = self._repository.get_by_id(channel_id)
        if not channel:
            raise ValueError(f"Channel not found: {channel_id}")

        # 只处理钉钉类型配置
        if channel.type != "dingding":
            logger.info(f"[sync_channel_to_openclaw] Skipping non-dingtalk channel: {channel.type}")
            return False

        # stage 为 verify 或 online 时跳过同步
        if channel.stage in ("verify", "online"):
            logger.info(f"[sync_channel_to_openclaw] Skipping channel with stage={channel.stage}, channel_id={channel_id}")
            return False

        # 2. 从 config 中提取 client_id
        config = channel.config
        client_id = config.get("client_id")
        if not client_id:
            raise ValueError(f"client_id not found in channel config: {channel_id}")

        # 3. 获取 bind_bot_id，通过 BotService 获取 openclaw 配置路径
        bind_bot_id = channel.bind_bot_id
        if not bind_bot_id:
            raise ValueError(f"bind_bot_id not found for channel: {channel_id}")

        # 4. 获取 bot 工作目录，openclaw.json 位于工作目录下
        work_path = self._bot_service.get_bot_work_path(bot_id=bind_bot_id, user_id=channel.identity_id, engine_type='openclaw')
        openclaw_config_path = work_path / "openclaw.json"

        # 5. 如果文件不存在，抛出异常
        # if not openclaw_config_path.exists():
        #     raise FileNotFoundError(f"openclaw.json not found: {openclaw_config_path}")

        # 6. 获取 device_fs 并使用 JsonConfigFile 操作配置
        ctx = self._resolver.resolve_for_bot(bind_bot_id, channel.identity_id)
        device_fs = self._device_fs_dispatcher.dispatch(ctx)
        json_config = await JsonConfigFile.load(openclaw_config_path, device_fs)

        # openclaw 的 dingtalk 配置路径: channels.dingtalk.accounts.{client_id}
        dingtalk_key = f"channels.dingtalk.accounts.{client_id}"

        if action == "apply":
            # 添加或更新配置
            # 检查是否存在单账号格式且 clientId 匹配
            single_account_key = "channels.dingtalk"
            single_account_data = json_config.get(single_account_key)

            if isinstance(single_account_data, dict) and single_account_data.get("clientId") == client_id:
                # 存在单账号格式且 clientId 匹配，更新单账号格式
                logger.info(f"[sync_channel_to_openclaw] Updating single-account format for {client_id}")
                target_key = single_account_key
            elif isinstance(single_account_data, dict) and "clientId" in single_account_data:
                # 存在单账号格式但 clientId 不匹配，需要转换为多账号格式
                existing_client_id = single_account_data.get("clientId")
                logger.info(f"[sync_channel_to_openclaw] Converting single-account to multi-account format. "
                           f"Existing: {existing_client_id}, New: {client_id}")

                # 先复制单账号数据到本地变量（避免引用问题）
                existing_account_data = dict(single_account_data)

                # 删除单账号格式（必须在设置多账号之前，避免路径冲突）
                json_config.delete(single_account_key)

                # 将现有单账号配置转换为多账号格式
                # 注意：enabled 保留在 dingtalk 层级，其他字段移到 accounts
                existing_account_key = f"channels.dingtalk.accounts.{existing_client_id}"
                for key, value in existing_account_data.items():
                    if value is not None and key != "enabled":
                        json_config.set(f"{existing_account_key}.{key}", value)

                # 恢复 enabled 到 dingtalk 层级
                if "enabled" in existing_account_data:
                    json_config.set("channels.dingtalk.enabled", existing_account_data["enabled"])

                # 设置目标为多账号格式
                target_key = dingtalk_key
            else:
                # 不存在单账号格式，使用多账号格式
                target_key = dingtalk_key

            # 字段映射：将 channel config 映射到 openclaw 格式
            # 注意：enabled 始终设置在 dingtalk 层级，其他字段根据格式设置
            dm_policy = config.get("dm_policy", "open")
            enable_streaming_cards = config.get("enable_streaming_cards", False)

            if target_key == single_account_key:
                # 单账号格式：所有字段都在 dingtalk 下
                field_mapping = {
                    f"{target_key}.enabled": True,
                    f"{target_key}.clientId": config.get("client_id"),
                    f"{target_key}.clientSecret": config.get("client_secret"),
                    f"{target_key}.robotCode": config.get("client_id"),
                    f"{target_key}.dmPolicy": dm_policy,
                    f"{target_key}.groupPolicy": "open",
                    f"{target_key}.messageType": config.get("message_type","card" if enable_streaming_cards else "markdown"),
                    f"{target_key}.cardTemplateId": config.get("card_template_id"),
                    f"{target_key}.cardTemplateKey": config.get("card_template_key"),
                }
            else:
                # 多账号格式：enabled 在 dingtalk 下，其他在 accounts.{client_id} 下
                field_mapping = {
                    "channels.dingtalk.enabled": True,
                    f"{target_key}.clientId": config.get("client_id"),
                    f"{target_key}.clientSecret": config.get("client_secret"),
                    f"{target_key}.robotCode": config.get("client_id"),
                    f"{target_key}.dmPolicy": dm_policy,
                    f"{target_key}.groupPolicy": "open",
                    f"{target_key}.messageType": config.get("message_type", "card" if enable_streaming_cards else "markdown"),
                    f"{target_key}.cardTemplateId": config.get("card_template_id"),
                    f"{target_key}.cardTemplateKey": config.get("card_template_key"),
                }

            # 设置配置（过滤掉 None 值）
            for key, value in field_mapping.items():
                if value is not None:
                    json_config.set(key, value)

            await json_config.save()
            logger.info(f"[sync_channel_to_openclaw] Applied channel {channel_id} to {openclaw_config_path}")

        elif action == "remove":
            # 删除配置 - 兼容两种格式：
            # 1. 多账号格式: channels.dingtalk.accounts.{client_id}
            # 2. 单账号格式: channels.dingtalk (直接包含 clientId)
            # 注意：删除时不删除 dingtalk 结构，而是设置 enabled: false
            removed = False

            # 尝试删除多账号格式 (accounts.{client_id})
            if json_config.exists(dingtalk_key):
                json_config.delete(dingtalk_key)
                removed = True
                logger.info(f"[sync_channel_to_openclaw] Removed account {client_id} from accounts")

                # 检查是否还有其他账号
                accounts_key = "channels.dingtalk.accounts"
                accounts_data = json_config.get(accounts_key)
                if accounts_data == {} or accounts_data is None:
                    # 没有账号了，删除 accounts 并设置 enabled: false
                    if json_config.exists(accounts_key):
                        json_config.delete(accounts_key)
                        logger.info(f"[sync_channel_to_openclaw] Removed empty {accounts_key}")

                    # 设置 enabled: false（保留 dingtalk 结构）
                    json_config.set("channels.dingtalk.enabled", False)
                    logger.info(f"[sync_channel_to_openclaw] Set enabled to false for dingtalk")

            # 尝试删除单账号格式 (直接检查 dingtalk.clientId)
            elif json_config.exists("channels.dingtalk"):
                dingtalk_data = json_config.get("channels.dingtalk")
                if isinstance(dingtalk_data, dict) and dingtalk_data.get("clientId") == client_id:
                    # 匹配到单账号格式，清空配置但保留结构，设置 enabled: false
                    # 删除所有账号相关字段，只保留 enabled: false
                    for key in list(dingtalk_data.keys()):
                        if key != "enabled":
                            json_config.delete(f"channels.dingtalk.{key}")
                    json_config.set("channels.dingtalk.enabled", False)
                    removed = True
                    logger.info(f"[sync_channel_to_openclaw] Cleared single-account config and set enabled to false for {client_id}")

            if removed:
                await json_config.save()
                logger.info(f"[sync_channel_to_openclaw] Removed channel {channel_id} from {openclaw_config_path}")
            else:
                logger.info(f"[sync_channel_to_openclaw] Config for {client_id} not found, nothing to remove")

        else:
            raise ValueError(f"Invalid action: {action}. Must be 'apply' or 'remove'")

        return True

    # 代码默认值
    DEFAULT_DINGTALK_VALUES = {
        "dmPolicy": "open",
        "groupPolicy": "open",
        "messageType": "markdown",
    }

    # 排除字段列表（不从模版继承）
    EXCLUDED_TEMPLATE_FIELDS = {
        "clientId", "clientSecret", "robotCode",
        "cardTemplateId", "cardTemplateKey", "messageType",
        "enabled",
    }

    async def generate_openclaw_configs(
        self,
        *,
        bot_id: str,
        owner_id: str,
    ) -> "OpenClawConfigs":
        """生成多环境 OpenClaw 配置文件

        使用模版替换模式：
        1. 加载 openclaw.json 作为模版
        2. 从模版中抽取可继承字段（排除指定字段）
        3. 用数据库值覆盖/合并到模版
        4. 统一使用多账号格式 accounts.{client_id}

        Args:
            bot_id: Bot ID
            owner_id: 用户身份 ID

        Returns:
            OpenClawConfigs 包含 verify 和 online 两个配置文件的 JSON 字符串

        Raises:
            FileNotFoundError: openclaw.json 不存在
        """
        # 1. 获取 bot 工作目录和 openclaw.json 路径
        work_path = self._bot_service.get_bot_work_path(
            bot_id=bot_id,
            user_id=owner_id,
            engine_type='openclaw'
        )
        openclaw_config_path = work_path / "openclaw.json"

        # 2. 获取 device_fs
        ctx = self._resolver.resolve_for_bot(bot_id, owner_id)
        device_fs = self._device_fs_dispatcher.dispatch(ctx)

        # 3. 加载当前 openclaw.json 作为模版
        json_config = await JsonConfigFile.load(openclaw_config_path, device_fs)
        template_config = json_config.to_dict()

        # 4. 查询所有钉钉配置，在内存中过滤 verify 和 online
        all_channels = self._repository.get_by_type_and_identity_ids(
            type="dingding",
            identity_ids=[owner_id],
            bind_bot_id=bot_id,
        )

        # 过滤生效的 verify 和 online 配置（status='1', stage='verify'/'online'）
        verify_channels = [
            ch for ch in all_channels
            if ch.status == "1" and ch.stage == "verify"
        ]
        online_channels = [
            ch for ch in all_channels
            if ch.status == "1" and ch.stage == "online"
        ]

        # 5. 提取钉钉配置和模版（避免重复提取）
        dingtalk_config = None
        if "channels" in template_config and "dingtalk" in template_config["channels"]:
            dingtalk_config = template_config["channels"]["dingtalk"]
        dingtalk_template = self._extract_dingtalk_template(dingtalk_config)

        # 6. 使用模版替换生成配置
        verify_config = self._apply_template(template_config, verify_channels, dingtalk_template)
        online_config = self._apply_template(template_config, online_channels, dingtalk_template)

        # 7. 生成 eval 配置：无条件生成，逻辑删除钉钉渠道（设置 enabled=False）
        eval_config = self._apply_template(template_config, [], dingtalk_template)

        # 8. 序列化为 JSON 字符串
        return OpenClawConfigs(
            verify=json.dumps(verify_config, indent=2, ensure_ascii=False),
            online=json.dumps(online_config, indent=2, ensure_ascii=False),
            eval=json.dumps(eval_config, indent=2, ensure_ascii=False),
        )

    def _extract_dingtalk_template(self, dingtalk_config: dict | None) -> dict:
        """从钉钉配置中抽取模版（排除指定字段后全部继承）

        从单账号或多账号格式中抽取可继承字段作为模版。
        采用排除法：移除排除列表中的字段，其他字段全部继承。

        对于多账号格式：合并所有账号的可继承字段（后覆盖前）。

        Args:
            dingtalk_config: dingtalk 配置字典，可能为 None 或空

        Returns:
            模版字典，包含可继承字段
            无钉钉配置时返回代码默认值
        """
        template = {
            "dmPolicy": self.DEFAULT_DINGTALK_VALUES["dmPolicy"],
            "groupPolicy": self.DEFAULT_DINGTALK_VALUES["groupPolicy"],
        }

        # 无钉钉配置，返回默认值
        if not dingtalk_config:
            return template

        if "accounts" in dingtalk_config:
            # 多账号格式：合并所有账号的可继承字段（后覆盖前）
            accounts = dingtalk_config["accounts"]
            for account in accounts.values():
                for field, value in account.items():
                    if field not in self.EXCLUDED_TEMPLATE_FIELDS:
                        template[field] = value
        else:
            # 单账号格式：排除指定字段后全部继承
            for field, value in dingtalk_config.items():
                if field not in self.EXCLUDED_TEMPLATE_FIELDS:
                    template[field] = value

        return template

    def _apply_template(
        self,
        template_config: dict,
        channels: list[ChannelRecord],
        dingtalk_template: dict,
    ) -> dict:
        """将数据库配置应用到模版

        流程:
        1. 深拷贝模版配置
        2. 提取钉钉模版（_template 字段），获取默认值
        3. 遍历数据库配置，构建账号配置：
           - clientId/clientSecret/robotCode: 从数据库获取
           - cardTemplateId/cardTemplateKey: 从数据库获取
           - messageType: 使用代码默认值
           - 其他字段: 模版值 > 代码默认值
        4. 统一转换为多账号格式 accounts.{client_id}
        5. 无数据库配置时：enabled=false（禁用钉钉）
           有数据库配置时：enabled=true

        Args:
            template_config: 原始配置（可能含模版）
            channels: 数据库中的钉钉配置列表
            dingtalk_template: 预提取的钉钉模版

        Returns:
            应用配置后的完整配置
        """
        result = copy.deepcopy(template_config)

        # 移除原有的钉钉配置
        if "channels" in result and "dingtalk" in result["channels"]:
            del result["channels"]["dingtalk"]

        if "channels" not in result:
            result["channels"] = {}
        result["channels"]["dingtalk"] = {"enabled": False}

        # 无数据库配置时，设置 enabled=False
        if not channels:
            return result

        # 有数据库配置，构建账号配置
        result["channels"]["dingtalk"] = {
            "enabled": True,
            "accounts": {},
        }

        for channel in channels:
            cfg = channel.config
            client_id = cfg.get("client_id")
            if not client_id:
                continue

            # 构建账号配置：模版默认值 + 代码默认值 + 数据库值
            account_config = {}

            # 1. 先填充代码默认值
            account_config.update(self.DEFAULT_DINGTALK_VALUES)

            # 2. 再用模版默认值覆盖（如果有的话）
            account_config.update(dingtalk_template)

            # 3. 最后用数据库值覆盖
            account_config["clientId"] = client_id
            account_config["clientSecret"] = cfg.get("client_secret")
            account_config["robotCode"] = client_id

            # 数据库可选字段
            if cfg.get("card_template_id"):
                account_config["cardTemplateId"] = cfg["card_template_id"]
            if cfg.get("card_template_key"):
                account_config["cardTemplateKey"] = cfg["card_template_key"]

            # enable_streaming_cards 影响 messageType
            enable_streaming_cards = cfg.get("enable_streaming_cards", False)
            if enable_streaming_cards:
                account_config["messageType"] = "card"

            # 移除 None 值
            account_config = {k: v for k, v in account_config.items() if v is not None}

            result["channels"]["dingtalk"]["accounts"][client_id] = account_config

        return result


@dataclass
class OpenClawConfigs:
    """多环境 OpenClaw 配置"""
    verify: str  # openclaw_verify.json 内容
    online: str  # openclaw_online.json 内容
    eval: str  # openclaw_eval.json 内容，钉钉渠道已禁用
