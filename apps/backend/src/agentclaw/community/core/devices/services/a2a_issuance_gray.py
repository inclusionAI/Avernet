"""A2A 签发面用户灰度门（设计：docs/superpowers/specs/2026-10-27-a2a-user-issuance-gray-release-design.md）。

分两层：
- 纯判定层（``parse_gray_config`` / ``is_user_in_gray``）——无 IO，语义矩阵见设计文档 §3；
- 门服务 ``A2aIssuanceGate``——直读 ``ConfigRepositoryProtocol`` 判定单次签发
  是否对 ``staff_id`` 暴露 A2A face。

fail 方向（每条都在测试钉板）：
- 配置**键不存在** / 显式 ``enabled: false`` → **不门控**：维持阶段二的加性
  签发，存量行为零变化；只有配置明确存在且合法才开启管控。
- 配置记录存在但值为 JSON ``null`` / 空串 / 空白 / 任何结构非法，以及
  DB 读取异常 / ``staff_id`` 为空 → **fail-closed 拒发**：运营已表达管控
  意图，判不动就闭门，绝不误全量。

存在性从 repo 判定而不是从 ``SystemConfigService.get_config`` 的返回值：
后者把「键不存在」和「值是 'null'」都折叠成 Python ``None``（_parse_value
经 json.loads），两者的运营含义恰好相反（未管控 vs 半配置事故）。
``ConfigRepository.get_config_by_key`` 的 record 携带**原始字符串**
``config_value``（未 parse），三态因此可分。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:
    from agentclaw.community.core.system_config import ConfigRepositoryProtocol

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
    """把**已知存在**的配置值解析为灰度参数。

    Returns:
        ``None`` —— 显式 ``enabled: false``（运营解除门控，全量形态）。
        其余 —— 门控生效。任何非法输入（null/空串/空白/坏 JSON/结构残缺）
        都解析为封闭名单（fail-closed），绝不退化为 None（未门控）。
        「键不存在」不在本函数语义内——存在性由 ``A2aIssuanceGate``
        从 repo record 判定后才调用到这里。
    """
    data: Any
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return _closed_gate()  # 空串/空白/坏 JSON 同归此处
    else:
        data = raw
    if not isinstance(data, dict):
        return _closed_gate()
    # ``enabled`` 缺失/null/非布尔（"yes"/1 等）一律 fail-closed；唯一解除
    # 门控的正形是显式布尔 false（键不存在在 gate 层已放开）。
    enabled = data.get("enabled")
    if enabled is False:
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

    直读 ``ConfigRepositoryProtocol``（record 的 ``config_value`` 是未 parse
    的原始字符串）：配置项记录是否存在即三态的判据——存在而值为
    null/空是事故，缺失是未管控。每次调用现读（连接签发低频，单次
    索引查询即可，不引入 TTL 缓存复杂度）；配置即改即生效，无需重启。
    """

    def __init__(self, config_repository: "ConfigRepositoryProtocol") -> None:
        self._repo = config_repository

    def _load_response(self, staff_id: str | None) -> bool:
        env = get_current_env()
        category = self._repo.get_category(
            category=A2A_ISSUANCE_GRAY_CONFIG_CATEGORY, env=env
        )
        if category is None:
            return True  # 分类未建 = 未配置，不加门
        record = self._repo.get_config_by_key(
            parent_id=category.id, config_key=A2A_ISSUANCE_GRAY_CONFIG_KEY
        )
        if record is None:
            return True  # 键不存在 = 未配置，不加门
        config = parse_gray_config(record.config_value)
        if config is None:
            return True  # 显式 enabled:false = 解除门控（全量形态）
        allowed = is_user_in_gray(config, staff_id)
        logger.info(
            "[a2a_gray] decision=%s mode=%s percent=%s staff=%s",
            "allow" if allowed else "withhold",
            config.mode,
            config.percent,
            _masked(staff_id),
        )
        return allowed

    def allows_face(self, staff_id: str | None) -> bool:
        try:
            return self._load_response(staff_id)
        except Exception:
            logger.warning(
                "[a2a_gray] config read failed, withholding face (fail-closed)",
                exc_info=True,
            )
            return False