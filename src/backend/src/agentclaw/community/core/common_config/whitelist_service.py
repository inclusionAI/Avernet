"""通用白名单判断服务。

该服务不直接访问数据库，只组合使用 ``CommonConfigService`` 读取
``ac_common_config.param_value``，并在这里承载白名单开关语义。
"""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.common_config.service import CommonConfigService


class CommonWhiteListService:
    """基于通用配置的白名单开关判断服务。"""

    @inject
    def __init__(self, common_config_service: CommonConfigService) -> None:
        self._common_config_service = common_config_service

    def is_bot_feature_enabled(
        self,
        *,
        business_code: str,
        param_code: str,
        owner_id: str,
        bot_id: str,
        env: str,
        default: bool = False,
    ) -> bool:
        """判断指定 bot 是否启用某个灰度功能。

        判断顺序：
        1. 通过 CommonConfigService 按 business_code + param_code + env
           读取启用状态下的配置值；
        2. ``enable_all is True`` 时直接放行；
        3. ``enable_all is False`` 时，按 owner_id + bot_id 精确匹配
           ``whitelist``；
        4. 配置缺失、配置禁用、格式异常或未命中时返回 default。
        """
        value = self._common_config_service.get_value(
            business_code=business_code,
            param_code=param_code,
            env=env,
            default=None,
            only_enabled=True,
        )
        if not isinstance(value, dict):
            return default

        enable_all = value.get("enable_all")
        if enable_all is True:
            return True
        if enable_all is not False:
            return default

        whitelist = value.get("whitelist", [])
        if not isinstance(whitelist, list):
            return default
        return self._match_bot_whitelist(
            whitelist,
            owner_id=owner_id,
            bot_id=bot_id,
        )

    @staticmethod
    def _match_bot_whitelist(
        whitelist: list[Any],
        *,
        owner_id: str,
        bot_id: str,
    ) -> bool:
        for item in whitelist:
            if not isinstance(item, dict):
                continue
            if str(item.get("owner_id")) == str(owner_id) and str(
                item.get("bot_id")
            ) == str(bot_id):
                return True
        return False
