"""A2A 签发面用户灰度门（设计：docs/superpowers/specs/2026-10-27-a2a-user-issuance-gray-release-design.md）。

分两层：
- 纯判定层（``parse_gray_config`` / ``is_user_in_gray``）——无 IO，语义矩阵见设计文档 §3；
- 门服务 ``A2aIssuanceGate``——读 system_config 判定单次签发是否对
  ``staff_id`` 暴露 A2A face。

fail 方向（每条都在测试钉板）：
- 配置缺失 / ``enabled: false`` → **不门控**：维持阶段二的加性签发，
  存量行为零变化；只有配置明确存在且合法才开启管控。
- 配置存在但任何字段非法 / DB 读取异常 / ``staff_id`` 为空 →
  **fail-closed 拒发**：运营已表达管控意图，判不动就闭门，绝不误全量。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from agentclaw.community.utils.env_utils import get_current_env

logger = logging.getLogger(__name__)

A2A_ISSUANCE_GRAY_CONFIG_CATEGORY = "system"
A2A_ISSUANCE_GRAY_CONFIG_KEY = "a2a_issuance_gray"

# 稳定 hash 的域前缀：换键名即换分桶，前缀避免与场景外同名输入碰撞。
_GRAY_HASH_DOMAIN = "a2a-gray:"

_WHITELIST_MODE = "whitelist"
_PERCENT_MODE = "percent"


@dataclass(frozen=True)
class A2aIssuanceGrayConfig:
    """一次可生效的灰度判定参数（不可变快照）。"""

    mode: str
    users: frozenset[str] = frozenset()
    percent: int = 0


def _closed_gate() -> A2aIssuanceGrayConfig:
    """fail-closed 形态：空名单，对任何人都不放行。"""
    return A2aIssuanceGrayConfig(mode=_WHITELIST_MODE, users=frozenset())


def parse_gray_config(raw: Any) -> A2aIssuanceGrayConfig | None:
    """把配置面原始值解析为灰度参数。

    Returns:
        ``None`` —— 未配置/显式关闭：签发面不加门，保持加性语义。
        其余 —— 门控生效；任何非法输入都解析为封闭名单（fail-closed），
        而不是退化为 None（未门控）。
    """
    if raw is None or raw == "" or (isinstance(raw, str) and not raw.strip()):
        return None
    data: Any
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return _closed_gate()
    else:
        data = raw
    if not isinstance(data, dict):
        return _closed_gate()
    enabled = data.get("enabled")
    # 布尔之外不允许任何隐式真值（"yes"/1 等按 fail-closed 关门）；
    # 显式 false 是运营的解除门控操作（全量形态），非失效状态。
    if enabled is False or enabled is None:
        return None
    if enabled is not True:
        return _closed_gate()
    mode = data.get("mode")
    if mode == _WHITELIST_MODE:
        users = data.get("users")
        if not isinstance(users, list) or not all(
            isinstance(u, str) for u in users
        ):
            return _closed_gate()
        return A2aIssuanceGrayConfig(
            mode=_WHITELIST_MODE, users=frozenset(users)
        )
    if mode == _PERCENT_MODE:
        percent = data.get("percent")
        if not isinstance(percent, int) or isinstance(percent, bool):
            return _closed_gate()
        if not 0 <= percent <= 100:
            return _closed_gate()
        return A2aIssuanceGrayConfig(mode=_PERCENT_MODE, percent=percent)
    return _closed_gate()


def is_user_in_gray(config: A2aIssuanceGrayConfig, staff_id: str | None) -> bool:
    """判定 ``staff_id`` 是否命中灰度。空身份恒不入选（fail-closed）。"""
    if not staff_id:
        return False
    if config.mode == _WHITELIST_MODE:
        return staff_id in config.users
    digest = hashlib.sha256((_GRAY_HASH_DOMAIN + staff_id).encode()).digest()
    return int.from_bytes(digest[:8], "big") % 100 < config.percent


def _masked(staff_id: str | None) -> str:
    if not staff_id:
        return "<empty>"
    return f"{staff_id[:2]}***"


class A2aIssuanceGate:
    """单次签发的 A2A face 门。

    每次调用现读配置（连接签发是低频路径，单次索引查询即可，
    不引入 TTL 缓存的复杂度）；配置即改即生效，无需重启。
    """

    def __init__(self, config_service: Any) -> None:
        self._config_service = config_service

    def allows_face(self, staff_id: str | None) -> bool:
        try:
            raw = self._config_service.get_config(
                category=A2A_ISSUANCE_GRAY_CONFIG_CATEGORY,
                config_key=A2A_ISSUANCE_GRAY_CONFIG_KEY,
                env=get_current_env(),
            )
        except Exception:
            logger.warning(
                "[a2a_gray] config read failed, withholding face (fail-closed)",
                exc_info=True,
            )
            return False
        config = parse_gray_config(raw)
        if config is None:
            # 未配置不门控：不必刷日志噪音，静默维持加性签发。
            return True
        allowed = is_user_in_gray(config, staff_id)
        logger.info(
            "[a2a_gray] decision=%s mode=%s percent=%s staff=%s",
            "allow" if allowed else "withhold",
            config.mode,
            config.percent,
            _masked(staff_id),
        )
        return allowed