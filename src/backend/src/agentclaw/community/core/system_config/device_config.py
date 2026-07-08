from typing import Any

from injector import inject

from agentclaw.community.core.system_config.service import SystemConfigService
from agentclaw.community.log import get_logger

logger = get_logger()

# ============================================================
# 设备配置服务（独立类）
# ============================================================


class DeviceConfigService:
    """设备配置服务

    提供设备相关的配置管理功能：
    - 设备分配名单管理（按工号 / 按 template_type）
    - 默认 Provider 管理
    - 根据员工工号 + bot 属性获取 Provider

    使用 SystemConfigService 作为底层配置存储。
    """

    # 设备分类标识
    CATEGORY = "device"

    # 配置键
    KEY_DEFAULT_PROVIDER = "default_provider"
    KEY_ARCA_USERS = "arca_users"
    KEY_BAAS_USERS = "baas_users"
    KEY_TEMPLATE_TYPE_PROVIDER_MAP = "template_type_provider_map"
    KEY_PERSONAL_BOT_BAAS_DISABLE = "personal_bot_baas_disable"
    KEY_MAX_DEVICES = "max_devices_per_entity"
    KEY_ALLOCATION_MODE = "allocation_mode"

    # 合法 provider 集合（daas 已下线，本期清理）
    VALID_PROVIDERS = ("arca", "baas")

    @inject
    def __init__(self, config_service: SystemConfigService):
        self._config = config_service

    def get_allocation_provider(
        self,
        *,
        staff_id: str,
        env: str,
        template_type: str | None = None,
        bot_type: str | None = None,
    ) -> str:
        """根据员工工号 + bot 属性获取设备分配的 Provider

        路由优先级（从高到低）：
        1. 紧急回退：``bot_type=="personal"`` 且 ``personal_bot_baas_disable=true`` → arca
        2. ``template_type → provider`` 映射命中 → 用 map 的值
        3. ``staff_id`` 在 arca_users 白名单 → arca
        4. ``staff_id`` 在 baas_users 白名单 → baas
        5. default_provider（默认 ``arca``）

        Args:
            staff_id: 员工工号
            env: 环境标识
            template_type: bot 模板类型（personalCoding / applicationCoding 等）
            bot_type: bot 业务类型（personal / service / desktop）

        Returns:
            Provider 名称 ('arca' 或 'baas')
        """
        # 1. 紧急回退：personal bot 全部强制走 arca
        if bot_type == "personal":
            disabled = self._config.get_config(
                category=self.CATEGORY, config_key=self.KEY_PERSONAL_BOT_BAAS_DISABLE, env=env
            )
            if disabled:
                logger.info(
                    f"[get_allocation_provider] personal_bot_baas_disable on; "
                    f"staff_id={staff_id} -> arca (forced)"
                )
                return "arca"

        # 2. template_type 映射
        if template_type:
            tpl_map = self._config.get_config(
                category=self.CATEGORY, config_key=self.KEY_TEMPLATE_TYPE_PROVIDER_MAP, env=env
            ) or {}
            mapped = tpl_map.get(template_type)
            if mapped in self.VALID_PROVIDERS:
                logger.info(
                    f"[get_allocation_provider] template_type={template_type} -> {mapped} "
                    f"(staff_id={staff_id})"
                )
                return mapped

        # 3. arca_users 白名单
        arca_list = self._config.get_config(
            category=self.CATEGORY, config_key=self.KEY_ARCA_USERS, env=env
        ) or []
        if staff_id in arca_list:
            logger.info(f"[get_allocation_provider] staff_id={staff_id} -> arca (whitelist)")
            return "arca"

        # 4. baas_users 白名单
        baas_list = self._config.get_config(
            category=self.CATEGORY, config_key=self.KEY_BAAS_USERS, env=env
        ) or []
        if staff_id in baas_list:
            logger.info(f"[get_allocation_provider] staff_id={staff_id} -> baas (whitelist)")
            return "baas"

        # 5. default_provider
        default_provider = self._config.get_config(
            category=self.CATEGORY, config_key=self.KEY_DEFAULT_PROVIDER, env=env
        ) or "arca"
        if default_provider not in self.VALID_PROVIDERS:
            logger.warning(
                f"[get_allocation_provider] invalid default_provider={default_provider!r} "
                f"in config, falling back to arca"
            )
            default_provider = "arca"
        logger.info(f"[get_allocation_provider] staff_id={staff_id} -> {default_provider} (default)")
        return default_provider

    def add_to_allocation_list(
        self, *, staff_ids: list[str], provider: str, env: str, creator: str | None = None
    ) -> int:
        """添加员工到分配名单

        Args:
            staff_ids: 员工工号列表
            provider: Provider 名称 ('arca' 或 'baas')
            env: 环境标识
            creator: 操作人

        Returns:
            实际新增数量
        """
        if provider not in self.VALID_PROVIDERS:
            raise ValueError(f"Invalid provider: {provider}")

        config_key = self.KEY_ARCA_USERS if provider == "arca" else self.KEY_BAAS_USERS
        current_list = self._config.get_config(
            category=self.CATEGORY, config_key=config_key, env=env
        ) or []

        original_count = len(current_list)
        updated_list = list(set(current_list + staff_ids))
        added_count = len(updated_list) - original_count

        self._config.set_config(
            category=self.CATEGORY,
            config_key=config_key,
            config_value=updated_list,
            env=env,
            description=f"{provider.upper()} 设备分配名单",
            operator=creator,
        )
        logger.info(f"[add_to_allocation_list] Added {added_count} staff to {config_key}")
        return added_count

    def remove_from_allocation_list(
        self, *, staff_ids: list[str], provider: str, env: str, creator: str | None = None
    ) -> int:
        """从分配名单移除员工

        Args:
            staff_ids: 员工工号列表
            provider: Provider 名称
            env: 环境标识
            creator: 操作人

        Returns:
            实际移除数量
        """
        if provider not in self.VALID_PROVIDERS:
            raise ValueError(f"Invalid provider: {provider}")

        config_key = self.KEY_ARCA_USERS if provider == "arca" else self.KEY_BAAS_USERS
        current_list = self._config.get_config(
            category=self.CATEGORY, config_key=config_key, env=env
        ) or []

        original_count = len(current_list)
        updated_list = [s for s in current_list if s not in staff_ids]
        removed_count = original_count - len(updated_list)

        self._config.set_config(
            category=self.CATEGORY,
            config_key=config_key,
            config_value=updated_list,
            env=env,
            operator=creator,
        )
        logger.info(f"[remove_from_allocation_list] Removed {removed_count} staff from {config_key}")
        return removed_count

    def get_allocation_lists(self, *, env: str) -> dict[str, Any]:
        """获取所有分配名单 + 灰度配置

        Args:
            env: 环境标识

        Returns:
            分配名单配置
        """
        return {
            "arca_users": self._config.get_config(
                category=self.CATEGORY, config_key=self.KEY_ARCA_USERS, env=env
            ) or [],
            "baas_users": self._config.get_config(
                category=self.CATEGORY, config_key=self.KEY_BAAS_USERS, env=env
            ) or [],
            "template_type_provider_map": self._config.get_config(
                category=self.CATEGORY, config_key=self.KEY_TEMPLATE_TYPE_PROVIDER_MAP, env=env
            ) or {},
            "personal_bot_baas_disable": bool(self._config.get_config(
                category=self.CATEGORY, config_key=self.KEY_PERSONAL_BOT_BAAS_DISABLE, env=env
            )),
            "default_provider": self._config.get_config(
                category=self.CATEGORY, config_key=self.KEY_DEFAULT_PROVIDER, env=env
            ) or "arca",
        }

    def set_default_provider(self, *, provider: str, env: str, creator: str | None = None) -> None:
        """设置默认设备 Provider

        Args:
            provider: Provider 名称
            env: 环境标识
            creator: 操作人
        """
        if provider not in self.VALID_PROVIDERS:
            raise ValueError(f"Invalid provider: {provider}")

        self._config.set_config(
            category=self.CATEGORY,
            config_key=self.KEY_DEFAULT_PROVIDER,
            config_value=provider,
            env=env,
            description="默认设备 Provider",
            operator=creator,
        )
        logger.info(f"[set_default_provider] Default provider set to {provider}")

    def set_template_type_provider_map(
        self, *, mapping: dict[str, str], env: str, creator: str | None = None
    ) -> None:
        """设置 template_type → provider 映射

        Args:
            mapping: template_type → provider 映射，所有 provider 必须在 VALID_PROVIDERS 内
            env: 环境标识
            creator: 操作人
        """
        for tpl_type, provider in mapping.items():
            if provider not in self.VALID_PROVIDERS:
                raise ValueError(
                    f"Invalid provider {provider!r} for template_type {tpl_type!r}"
                )

        self._config.set_config(
            category=self.CATEGORY,
            config_key=self.KEY_TEMPLATE_TYPE_PROVIDER_MAP,
            config_value=mapping,
            env=env,
            description="template_type → provider 映射（灰度个人 Bot 走 BaaS）",
            operator=creator,
        )
        logger.info(f"[set_template_type_provider_map] {mapping}")

    def set_personal_bot_baas_disable(
        self, *, disabled: bool, env: str, creator: str | None = None
    ) -> None:
        """紧急回退开关：true 时所有 personal bot 强制走 arca。"""
        self._config.set_config(
            category=self.CATEGORY,
            config_key=self.KEY_PERSONAL_BOT_BAAS_DISABLE,
            config_value=bool(disabled),
            env=env,
            description="紧急回退：true 时强制 personal bot 走 arca",
            operator=creator,
        )
        logger.info(f"[set_personal_bot_baas_disable] {disabled}")

    def get_max_devices(self, *, env: str) -> int:
        """获取每个实体最大设备数量"""
        value = self._config.get_config(
            category=self.CATEGORY, config_key=self.KEY_MAX_DEVICES, env=env
        )
        return int(value) if value is not None else 5

    def set_max_devices(self, *, max_devices: int, env: str, creator: str | None = None) -> None:
        """设置每个实体最大设备数量"""
        self._config.set_config(
            category=self.CATEGORY,
            config_key=self.KEY_MAX_DEVICES,
            config_value=max_devices,
            env=env,
            description="每个实体最大设备数量",
            operator=creator,
        )

    def get_allocation_mode(self, *, env: str) -> str:
        """获取设备分配模式"""
        value = self._config.get_config(
            category=self.CATEGORY, config_key=self.KEY_ALLOCATION_MODE, env=env
        )
        return value or "multi"

    def set_allocation_mode(self, *, mode: str, env: str, creator: str | None = None) -> None:
        """设置设备分配模式"""
        if mode not in ("single", "multi"):
            raise ValueError(f"Invalid mode: {mode}")

        self._config.set_config(
            category=self.CATEGORY,
            config_key=self.KEY_ALLOCATION_MODE,
            config_value=mode,
            env=env,
            description="设备分配模式: single=单设备, multi=多设备",
            operator=creator,
        )
