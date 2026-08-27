"""任务派发 ``claim_on`` JOIN 灰度开关(搜推候选 ∩ task_claim_mode-on 名单)。

线上已有 OOB 预授权部署 bot 的派发链路不依赖本开关(直按 assignee 派发)。
若 JOIN 默认即开,未在前端开启「任务认领」(claim_mode)的 bot 会在派发侧被判未命中而被降级/跳过,
影响现有功能。故开关默认关闭,经 HTTP 端点显式开启:
- 关闭(``False``,默认)→ 派发不做 claim_on 名单交集,直按 assignee 派发(当前线上行为,不回归)。
- 开启(``True``)→ 派发对 LLM 决出的 assignee 做「搜推候选 ∩ claim_on 名单」JOIN:未在名单的候选
  记 ``unauthorized_bots`` 降 MISS / 部分降级,引导 owner 前端开启「任务认领」grant 公共 api-key。

存储:复用 ``SystemConfigServiceProtocol`` KV(category=``task``,key=``claim_join_filter_enabled``)
——集群级、跨副本共享、持久化(重启后 HA 仍按库值,默认关闭)。读热点加 ~15s TTL 缓存(写穿失效),
默认关闭 + 读取异常 fail-open(=False),确保任何情况下不回归现有派发行为。env 经 ``get_current_env()``
归一(prod/pre/dev)。
"""

from __future__ import annotations

import threading
import time
from typing import Protocol, runtime_checkable

from injector import inject

from agentclaw.community.plugin_api.system_config import SystemConfigServiceProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

CATEGORY = "task"
KEY = "claim_join_filter_enabled"
_DESCRIPTION = (
    "任务派发 claim_on JOIN 灰度开关(false=关,直按 assignee 派发;true=做「搜推候选 ∩ claim_on 名单」交集)"
)
_CACHE_TTL_S = 15.0

_TRUE_LITERALS = ("true", "1", "yes")


@runtime_checkable
class TaskClaimJoinGateProtocol(Protocol):
    """派发 claim_on JOIN 运行时开关(默认关闭,HTTP 显式开启)。"""

    def is_enabled(self) -> bool:
        """派发热路径读(带 TTL 缓存,读取异常 fail-open 返回 False,默认关闭)。"""
        ...

    def get_enabled(self, *, env: str) -> bool:
        """库侧真实读取(无缓存),供 GET 端点展示权威状态。默认/未配 → False。"""
        ...

    def set_enabled(
        self, *, enabled: bool, env: str, operator: str | None = None
    ) -> bool:
        """HTTP 开/关:落库并写穿本地缓存(跨副本经 KV + 各副本 TTL 收敛,≤TTL)。返回新值。"""
        ...


def _coerce_bool(value) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE_LITERALS


class TaskClaimJoinGate(TaskClaimJoinGateProtocol):
    """claim_on JOIN 开关实现:复用 corp 装配的 ``SystemConfigServiceProtocol`` KV。

    ``config`` 未装配(community/singlebox/纯测试)→ 恒返回 False(fail-open,不回归)。
    """

    @inject
    def __init__(self, config: SystemConfigServiceProtocol | None = None) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._cache: bool | None = None
        self._cache_ts: float = 0.0

    def _read(self, *, env: str) -> bool:
        if self._config is None:
            return False  # 未装配配置子系统 → fail-open(关闭)
        try:
            value = self._config.get_config(category=CATEGORY, config_key=KEY, env=env)
        except Exception as exc:  # noqa: BLE101 配置子系统未装/不可用 → fail-open(关闭)
            logger.debug(
                "[task][claim-join] get_config 读取异常 → fail-open(关):%s: %s",
                type(exc).__name__,
                exc,
            )
            return False
        return _coerce_bool(value)

    def is_enabled(self) -> bool:
        """派发热路径读取(带 TTL 缓存 + fail-open);当前 env 经 get_current_env()。"""
        now = time.monotonic()
        with self._lock:
            if self._cache is None or (now - self._cache_ts) > _CACHE_TTL_S:
                env = get_current_env()
                self._cache = self._read(env=env)
                self._cache_ts = now
                logger.debug(
                    "[task][claim-join] 缓存刷新 env=%s enabled=%s", env, self._cache
                )
            return self._cache

    def get_enabled(self, *, env: str) -> bool:
        """库侧真实读取(无缓存),供 GET 端点。"""
        return self._read(env=env)

    def set_enabled(
        self, *, enabled: bool, env: str, operator: str | None = None
    ) -> bool:
        if self._config is None:
            # 未装配配置子系统:不开关(不落库),恒表示关闭 → 调用方仅可读;写视为失败但 fail-open。
            logger.warning(
                "[task][claim-join] SystemConfigServiceProtocol 未装配,set_enabled 忽略(恒关闭)"
            )
            return False
        self._config.set_config(
            category=CATEGORY,
            config_key=KEY,
            config_value=bool(enabled),
            env=env,
            description=_DESCRIPTION,
            creator=operator,
        )
        with self._lock:
            self._cache = bool(enabled)
            self._cache_ts = time.monotonic()
        logger.info(
            "[task][claim-join] claim_on JOIN 开关已设为 enabled=%s env=%s operator=%s",
            enabled,
            env,
            operator,
        )
        return bool(enabled)
