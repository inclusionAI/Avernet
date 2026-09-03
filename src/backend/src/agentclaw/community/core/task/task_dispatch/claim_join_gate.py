"""Generic task feature switches backed by the system-config KV store.

The task settings service owns the supported switch names and their storage
keys. ``TaskClaimJoinGate`` remains a compatibility adapter for the dispatch
post-filter, while new callers should use ``TaskSettingsServiceProtocol``.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol, runtime_checkable

from injector import inject

from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

CATEGORY = "task"
CLAIM_JOIN_FILTER = "claim_join_filter"
SEARCH_SKILL = "search_skill"
SKILL_REPORT = "skill_report_enabled"
CLAIM_JOIN_FILTER_KEY = "claim_join_filter_enabled"
# Backward-compatible names used by existing tests and callers.
KEY = CLAIM_JOIN_FILTER_KEY
SEARCH_SKILL_KEY = "search_skill_enabled"
SKILL_REPORT_KEY = "skill_report_enabled"
# TaskHarness 旁路巡检开关(默认关闭);through tasks/settings API (harness_poller).
HARNESS_POLLER = "harness_poller"
HARNESS_POLLER_KEY = "harness_poller_enabled"
_SETTING_KEYS = {
    CLAIM_JOIN_FILTER: CLAIM_JOIN_FILTER_KEY,
    SEARCH_SKILL: SEARCH_SKILL_KEY,
    SKILL_REPORT: SKILL_REPORT_KEY,
    HARNESS_POLLER: HARNESS_POLLER_KEY,
}
_CACHE_TTL_S = 15.0
_TRUE_LITERALS = ("true", "1", "yes", "on")


@runtime_checkable
class _SystemConfigStore(Protocol):
    def get_config(self, *, category: str, config_key: str, env: str) -> Any: ...

    def set_config(
        self,
        *,
        category: str,
        config_key: str,
        config_value: Any,
        env: str,
        description: str | None = None,
        operator: str | None = None,
    ) -> int: ...


@runtime_checkable
class TaskSettingsServiceProtocol(Protocol):
    """Generic task switch service."""

    def is_enabled(self, setting_type: str) -> bool: ...

    def get_enabled(self, *, setting_type: str, env: str) -> bool: ...

    def set_enabled(
        self,
        *,
        setting_type: str,
        enabled: bool,
        env: str,
        operator: str | None = None,
    ) -> bool: ...


class TaskSettingsService(TaskSettingsServiceProtocol):
    """Runtime task switch service with explicit per-setting defaults."""

    @inject
    def __init__(
        self,
        config: _SystemConfigStore | None = None,
        defaults: dict[str, bool] | None = None,
    ) -> None:
        self._config = config
        # skill_report 默认 True(走 skill HTTP 上报链路);关闭后任务统一改走
        # poller 拉取链路(predict：bot POST /callback/report),与 poller 互斥(不并存)。
        self._defaults = {
            CLAIM_JOIN_FILTER: False,
            SEARCH_SKILL: False,
            SKILL_REPORT: True,
            # TaskHarness 旁路巡检默认开启:常驻兜底(SLA 超时复位/FAILED 重派/PENDING 派发超时重搜推);
            # 事件驱动为主推进,此为旁路兜底。可经 tasks/settings harness_poller 跨副本热改关闭。
            HARNESS_POLLER: True,
            **(defaults or {}),
        }
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[bool, float]] = {}

    @staticmethod
    def _key(setting_type: str) -> str:
        try:
            return _SETTING_KEYS[setting_type]
        except KeyError:
            raise ValueError(f"unsupported task setting type: {setting_type}") from None

    def _default(self, setting_type: str) -> bool:
        if setting_type not in _SETTING_KEYS:
            raise ValueError(f"unsupported task setting type: {setting_type}")
        return bool(self._defaults.get(setting_type, False))

    @staticmethod
    def _coerce_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in _TRUE_LITERALS

    def _read(self, *, setting_type: str, env: str) -> bool:
        default = self._default(setting_type)
        if self._config is None:
            return default
        try:
            value = self._config.get_config(
                category=CATEGORY,
                config_key=self._key(setting_type),
                env=env,
            )
        except Exception as exc:  # noqa: BLE001 - switch reads fail open
            logger.debug(
                "[task][settings] read failed type=%s env=%s → default=%s: %s",
                setting_type,
                env,
                default,
                exc,
            )
            return default
        return self._coerce_bool(value, default)

    def is_enabled(self, setting_type: str) -> bool:
        self._key(setting_type)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(setting_type)
            if cached is not None and now - cached[1] <= _CACHE_TTL_S:
                return cached[0]
        env = get_current_env()
        value = self._read(setting_type=setting_type, env=env)
        with self._lock:
            self._cache[setting_type] = (value, now)
        logger.info(
            "[task][settings] read type=%s env=%s enabled=%s source=%s",
            setting_type,
            env,
            value,
            "system_config" if self._config is not None else "default",
        )
        return value

    def get_enabled(self, *, setting_type: str, env: str) -> bool:
        return self._read(setting_type=setting_type, env=env)

    def set_enabled(
        self,
        *,
        setting_type: str,
        enabled: bool,
        env: str,
        operator: str | None = None,
    ) -> bool:
        key = self._key(setting_type)
        if self._config is None:
            logger.warning("[task][settings] store unavailable, ignore set type=%s", setting_type)
            return self._default(setting_type)
        self._config.set_config(
            category=CATEGORY,
            config_key=key,
            config_value=bool(enabled),
            env=env,
            description=f"任务开关:{setting_type}",
            operator=operator,
        )
        with self._lock:
            self._cache[setting_type] = (bool(enabled), time.monotonic())
        logger.info(
            "[task][settings] set type=%s enabled=%s env=%s operator=%s",
            setting_type,
            enabled,
            env,
            operator,
        )
        return bool(enabled)


@runtime_checkable
class TaskClaimJoinGateProtocol(Protocol):
    """Compatibility contract for the claim_on JOIN post-filter."""

    def is_enabled(self) -> bool: ...

    def get_enabled(self, *, env: str) -> bool: ...

    def set_enabled(
        self, *, enabled: bool, env: str, operator: str | None = None
    ) -> bool: ...


class TaskClaimJoinGate(TaskClaimJoinGateProtocol):
    """Compatibility adapter over the generic task settings service."""

    @inject
    def __init__(
        self,
        config: _SystemConfigStore | None = None,
        settings: TaskSettingsServiceProtocol | None = None,
    ) -> None:
        self._settings = settings or TaskSettingsService(config=config)

    def is_enabled(self) -> bool:
        return self._settings.is_enabled(CLAIM_JOIN_FILTER)

    def get_enabled(self, *, env: str) -> bool:
        return self._settings.get_enabled(setting_type=CLAIM_JOIN_FILTER, env=env)

    def set_enabled(
        self, *, enabled: bool, env: str, operator: str | None = None
    ) -> bool:
        return self._settings.set_enabled(
            setting_type=CLAIM_JOIN_FILTER,
            enabled=enabled,
            env=env,
            operator=operator,
        )
