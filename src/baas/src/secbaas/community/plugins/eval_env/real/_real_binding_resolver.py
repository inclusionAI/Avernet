"""RealEvalBindingResolver — 评测绑定解析 Real 实现。

基于 ``EvalBindingResolverProtocol`` Plugin 实现插拔式替换。
"""

from __future__ import annotations

from typing import Any

from secbaas.community.logger import get_logger
from secbaas.community.spi.eval_env import EvalBindingResolverProtocol

logger = get_logger("core-bot-run")


class RealEvalBindingResolver(EvalBindingResolverProtocol):
    """评测绑定解析的 Real 实现。

    基于 ``EvalBindingResolverProtocol`` Plugin 实现插拔式替换。
    """

    def __init__(
        self,
        binding_repo: Any,
        system_config_service: Any,
    ) -> None:
        self._binding_repo = binding_repo
        self._system_config_service = system_config_service

    def resolve_eval_binding(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
    ) -> int | None:
        """解析评测环境的 binding_id。

        委托 ``binding_repo.find_eval_binding`` 查询。
        """
        try:
            binding = self._binding_repo.find_eval_binding(
                bot_id=bot_id,
                entity_id=entity_id,
                env=env,
            )
            if binding is not None:
                return binding.id
            return None
        except Exception:
            logger.warning(
                "[RealEvalBindingResolver] resolve_eval_binding 失败: bot_id=%s",
                bot_id,
            )
            return None

    def is_eval_env_enabled(self) -> bool:
        """检查评测环境功能是否启用。

        委托 ``system_config_service.get_config`` 查询。
        """
        try:
            if self._system_config_service is None:
                return False
            config = self._system_config_service.get_config(
                "AGENTCLAW_EVAL_ENV_ENABLED"
            )
            if config is None:
                return False
            return getattr(config, "conf_value", "").lower() in ("true", "1", "yes")
        except Exception:
            return False
