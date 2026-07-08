from typing import Any

from injector import inject

from agentclaw.community.core.system_config.service import SystemConfigService
from agentclaw.community.log import get_logger

logger = get_logger()


class ClusterConfigService:
    """集群配置服务

    提供集群相关的配置管理功能：
    - 备用租户流量控制（alt_traffic）
    - 备用租户灰度名单（alt_gray_users）

    使用 SystemConfigService 作为底层配置存储。
    """

    # 集群分类标识
    CATEGORY = "cluster"

    # 配置键
    KEY_ALT_TRAFFIC = "alt_traffic"
    KEY_ALT_GRAY_USERS = "alt_gray_users"
    KEY_TEMPLATE_WHITELIST = "template_whitelist"
    KEY_USE_AICODING_TENANT = "use_aicoding_tenant"

    @inject
    def __init__(self, config_service: SystemConfigService):
        self._config = config_service

    def get_alt_traffic(self, *, env: str) -> int:
        """获取备用租户流量控制值

        表示当前集群从哪个租户申请 arca 资源。
        - 0: 使用主租户（默认值）
        - 1: 使用备用租户

        Args:
            env: 环境标识

        Returns:
            int: alt_traffic 值，配置为空时返回默认值 0
        """
        value = self._config.get_config(
            category=self.CATEGORY, config_key=self.KEY_ALT_TRAFFIC, env=env
        )
        if value is None:
            logger.debug(f"[get_alt_traffic] no config found, returning default 0")
            return 0
        try:
            return int(value)
        except (ValueError, TypeError):
            logger.warning(f"[get_alt_traffic] invalid value: {value}, returning default 0")
            return 0

    def set_alt_traffic(self, *, value: int, env: str, creator: str | None = None) -> None:
        """设置备用租户流量控制值

        Args:
            value: alt_traffic 值（0 或 1）
            env: 环境标识
            creator: 操作人

        Raises:
            ValueError: 如果 value 不是 0 或 1
        """
        if value not in (0, 1):
            raise ValueError(f"Invalid alt_traffic value: {value}, must be 0 or 1")

        self._config.set_config(
            category=self.CATEGORY,
            config_key=self.KEY_ALT_TRAFFIC,
            config_value=value,
            env=env,
            description="备用租户流量控制: 0=主租户, 1=备用租户",
            creator=creator,
        )
        logger.info(f"[set_alt_traffic] alt_traffic set to {value}")

    def get_use_aicoding_tenant(self, *, env: str) -> bool:
        value = self._config.get_config(
            category=self.CATEGORY, config_key=self.KEY_USE_AICODING_TENANT, env=env
        )
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "yes")

    def set_use_aicoding_tenant(self, *, enabled: bool, env: str, creator: str | None = None) -> None:
        self._config.set_config(
            category=self.CATEGORY,
            config_key=self.KEY_USE_AICODING_TENANT,
            config_value=bool(enabled),
            env=env,
            description="全量切换 aicoding 引擎 arca bot 到新租户 (arcaantsecaiim)",
            creator=creator,
        )
        logger.info(f"[set_use_aicoding_tenant] set to {enabled} for env={env}")

    def get_alt_gray_users(self, *, env: str) -> dict[str, Any] | None:
        """获取备用租户灰度名单及灰度目标租户

        Returns:
            dict | None: 格式为 {"target_alt": int, "gray_users": list[str]}
            如果表里没有配置数据，返回 None
        """
        value = self._config.get_config(
            category=self.CATEGORY, config_key=self.KEY_ALT_GRAY_USERS, env=env
        )
        if value is None:
            logger.debug(f"[get_alt_gray_users] no config found, returning None")
            return None

        if isinstance(value, dict) and "target_alt" in value and "gray_users" in value:
            return value

        logger.warning(f"[get_alt_gray_users] invalid format: {value}")
        return None

    def set_alt_gray_users(
            self,
            *,
            target_alt: int,
            gray_users: list[str],
            env: str,
            creator: str | None = None,
    ) -> None:
        """设置备用租户灰度名单

        Args:
            target_alt: 灰度目标租户（0 或 1）
            gray_users: 灰度用户列表（staff_id 列表）
            env: 环境标识
            creator: 操作人

        Raises:
            ValueError: 如果 target_alt 不是 0 或 1
        """
        if target_alt not in (0, 1):
            raise ValueError(f"Invalid target_alt value: {target_alt}, must be 0 or 1")

        config_value = {
            "target_alt": target_alt,
            "gray_users": list(set(gray_users)),  # 去重
        }

        self._config.set_config(
            category=self.CATEGORY,
            config_key=self.KEY_ALT_GRAY_USERS,
            config_value=config_value,
            env=env,
            description="备用租户灰度名单及灰度目标租户",
            creator=creator,
        )
        logger.info(
            f"[set_alt_gray_users] target_alt={target_alt}, gray_users_count={len(gray_users)}"
        )

    def is_gray_user(self, *, staff_id: str, env: str) -> bool:
        """检查指定员工是否在灰度名单中

        Args:
            staff_id: 员工工号
            env: 环境标识

        Returns:
            bool: 如果员工在灰度名单中返回 True，没有配置时返回 False
        """
        gray_config = self.get_alt_gray_users(env=env)
        if gray_config is None:
            return False
        return staff_id in gray_config.get("gray_users", [])

    def get_template_whitelist(self, *, env: str) -> dict[str, Any] | None:
        """获取模板白名单配置

        Returns:
            dict | None: 格式为 {"template_id": str, "staff_ids": list[str]}
            如果表里没有配置数据，返回 None
        """
        value = self._config.get_config(
            category=self.CATEGORY, config_key=self.KEY_TEMPLATE_WHITELIST, env=env
        )
        if value is None:
            logger.debug("[get_template_whitelist] no config found, returning None")
            return None

        if isinstance(value, dict) and "template_id" in value and "staff_ids" in value:
            return value

        logger.warning("[get_template_whitelist] invalid format: %s", value)
        return None

    def set_template_whitelist(
            self,
            *,
            template_id: str,
            staff_ids: list[str],
            env: str,
            creator: str | None = None,
    ) -> None:
        """设置模板白名单

        Args:
            template_id: 白名单用户使用的 Arca 模板 ID
            staff_ids: 白名单工号列表
            env: 环境标识
            creator: 操作人
        """
        config_value = {
            "template_id": template_id,
            "staff_ids": list(set(staff_ids)),
        }

        self._config.set_config(
            category=self.CATEGORY,
            config_key=self.KEY_TEMPLATE_WHITELIST,
            config_value=config_value,
            env=env,
            description="模板白名单：白名单内工号使用指定 Arca 模板",
            creator=creator,
        )
        logger.info(
            "[set_template_whitelist] template_id=%s, staff_ids_count=%d",
            template_id, len(staff_ids),
        )

    def is_template_whitelist_user(self, *, staff_id: str, env: str) -> tuple[bool, str | None]:
        """检查指定员工是否在模板白名单中

        Args:
            staff_id: 员工工号
            env: 环境标识

        Returns:
            tuple[bool, str | None]: (是否命中白名单, 命中时返回配置的 template_id)
            未配置或不在白名单中时返回 (False, None)
        """
        whitelist_config = self.get_template_whitelist(env=env)
        if whitelist_config is None:
            return False, None

        staff_ids = whitelist_config.get("staff_ids", [])
        if staff_id in staff_ids:
            template_id = whitelist_config.get("template_id", "")
            logger.info(
                "[is_template_whitelist_user] staff_id=%s hit template whitelist, template_id=%s",
                staff_id, template_id,
            )
            return True, template_id

        return False, None
