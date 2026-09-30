"""原 ARCA bot 创建切 BaaS 的 DRM 灰度配置。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_branches import (
    SUPPORTED_ARCA_CREATE_BAAS_ROLLOUT_BRANCHES,
)
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.plugin_api.drm import DRMReaderPlugin


logger = get_logger()


@dataclass(frozen=True)
class ArcaBotCreateBaasRolloutRule:
    """一条 bot_type + engine_bucket 精确灰度规则。"""

    bot_type: str
    engine_bucket: str
    allow_all_users: bool = False
    allow_user_groups: tuple[str, ...] = ()
    allow_user_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArcaBotCreateBaasRolloutScope:
    """用户范围，可被 default_scope 和 rule 复用。"""

    allow_all_users: bool = False
    allow_user_groups: tuple[str, ...] = ()
    allow_user_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArcaBotCreateBaasRolloutConfig:
    """解析后的创建期灰度配置。

    只有 enabled 且 rules 完整合法时才会生效；任何结构问题都会退回
    disabled，避免半份配置影响创建路由。
    """

    enabled: bool = False
    version: str = ""
    user_groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    rules: tuple[ArcaBotCreateBaasRolloutRule, ...] = ()

    @classmethod
    def disabled(cls) -> ArcaBotCreateBaasRolloutConfig:
        return cls(enabled=False)


class ArcaBotCreateBaasRolloutConfigProvider:
    """从 DRM 读取原 ARCA 创建切 BaaS 的灰度配置。

    本期创建路由以 DRM 为唯一动态配置入口，不复用旧
    DeviceConfigService，避免两套配置同时决定 provider。
    """

    DRM_ID = (
        "Alipay.agentclaw:name=com.alipay.agentclaw.service.drm."
        "ArcaBotCreateBaasRollout.config,version=3.0@DRM"
    )

    VALID_BOT_TYPES = {"personal", "service"}
    ALLOW_SCOPE_KEYS = ("allow_all_users", "allow_user_groups", "allow_user_ids")
    MAX_LIST_SIZE = 1000

    def __init__(self, drm_reader: "DRMReaderPlugin") -> None:
        self._drm_reader = drm_reader

    def get(self) -> ArcaBotCreateBaasRolloutConfig:
        raw_value = self._drm_reader.read(self.DRM_ID)
        logger.info("[DRM] ArcaBotCreateBaasRollout value=%r", raw_value)
        return self._parse(raw_value)

    def _parse(self, raw_value: Any) -> ArcaBotCreateBaasRolloutConfig:
        if raw_value in (None, ""):
            return ArcaBotCreateBaasRolloutConfig.disabled()

        if isinstance(raw_value, str):
            try:
                payload = json.loads(raw_value)
            except json.JSONDecodeError as e:
                logger.warning(
                    "[ArcaBotCreateBaasRolloutConfigProvider] invalid JSON: %s",
                    e,
                )
                return ArcaBotCreateBaasRolloutConfig.disabled()
        elif isinstance(raw_value, dict):
            payload = raw_value
        else:
            logger.warning(
                "[ArcaBotCreateBaasRolloutConfigProvider] unsupported payload type: %s",
                type(raw_value).__name__,
            )
            return ArcaBotCreateBaasRolloutConfig.disabled()

        if not isinstance(payload, dict):
            return ArcaBotCreateBaasRolloutConfig.disabled()

        enabled = self._as_bool(payload.get("enabled"))
        if not enabled:
            return ArcaBotCreateBaasRolloutConfig.disabled()

        user_groups = self._parse_user_groups(payload.get("user_groups", {}))
        if user_groups is None:
            logger.warning(
                "[ArcaBotCreateBaasRolloutConfigProvider] invalid user_groups, "
                "fallback disabled"
            )
            return ArcaBotCreateBaasRolloutConfig.disabled()

        default_scope = None
        if "default_scope" in payload:
            default_scope = self._parse_scope(
                payload.get("default_scope"),
                user_groups=user_groups,
            )
            if default_scope is None:
                logger.warning(
                    "[ArcaBotCreateBaasRolloutConfigProvider] invalid default_scope, "
                    "fallback disabled"
                )
                return ArcaBotCreateBaasRolloutConfig.disabled()

        rules = self._parse_rules(
            payload.get("rules"),
            user_groups=user_groups,
            default_scope=default_scope,
        )
        if not rules:
            logger.warning(
                "[ArcaBotCreateBaasRolloutConfigProvider] incomplete rollout config, "
                "fallback disabled"
            )
            return ArcaBotCreateBaasRolloutConfig.disabled()

        return ArcaBotCreateBaasRolloutConfig(
            enabled=True,
            version=str(payload.get("version") or ""),
            user_groups=user_groups,
            rules=rules,
        )

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "on", "1", "yes"}
        return False

    def _safe_string_tuple(
        self,
        value: Any,
        *,
        allowed_values: set[str] | None = None,
    ) -> tuple[str, ...] | None:
        if not isinstance(value, list) or len(value) > self.MAX_LIST_SIZE:
            return None

        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return None
            normalized = item.strip()
            if not normalized:
                return None
            if allowed_values is not None and normalized not in allowed_values:
                return None
            result.append(normalized)
        if not result:
            return None
        return tuple(result)

    def _parse_user_groups(self, value: Any) -> dict[str, tuple[str, ...]] | None:
        if value is None:
            return {}
        if not isinstance(value, dict) or len(value) > self.MAX_LIST_SIZE:
            return None

        user_groups: dict[str, tuple[str, ...]] = {}
        for raw_name, raw_user_ids in value.items():
            group_name = self._safe_string(raw_name)
            user_ids = self._safe_string_tuple(raw_user_ids)
            if group_name is None or user_ids is None:
                return None
            user_groups[group_name] = user_ids
        return user_groups

    def _parse_rules(
        self,
        value: Any,
        *,
        user_groups: dict[str, tuple[str, ...]],
        default_scope: ArcaBotCreateBaasRolloutScope | None = None,
    ) -> tuple[ArcaBotCreateBaasRolloutRule, ...] | None:
        if not isinstance(value, list) or not value or len(value) > self.MAX_LIST_SIZE:
            return None

        rules: list[ArcaBotCreateBaasRolloutRule] = []
        seen_keys: set[tuple[str, str]] = set()
        for item in value:
            if not isinstance(item, dict):
                return None

            bot_type = self._safe_string(
                item.get("bot_type"),
                allowed_values=self.VALID_BOT_TYPES,
            )
            engine_bucket = self._safe_string(
                item.get("engine_bucket"),
            )
            if bot_type is None or engine_bucket is None:
                return None

            key = (bot_type, engine_bucket)
            if key not in SUPPORTED_ARCA_CREATE_BAAS_ROLLOUT_BRANCHES:
                return None
            if key in seen_keys:
                return None
            seen_keys.add(key)

            has_explicit_scope = any(key in item for key in self.ALLOW_SCOPE_KEYS)
            if has_explicit_scope:
                scope = self._parse_scope(item, user_groups=user_groups)
            else:
                scope = default_scope
            if scope is None:
                return None

            rules.append(
                ArcaBotCreateBaasRolloutRule(
                    bot_type=bot_type,
                    engine_bucket=engine_bucket,
                    allow_all_users=scope.allow_all_users,
                    allow_user_groups=scope.allow_user_groups,
                    allow_user_ids=scope.allow_user_ids,
                )
            )

        return tuple(rules)

    def _parse_scope(
        self,
        value: Any,
        *,
        user_groups: dict[str, tuple[str, ...]],
    ) -> ArcaBotCreateBaasRolloutScope | None:
        if not isinstance(value, dict):
            return None

        allow_all_users = self._as_bool(value.get("allow_all_users"))
        allow_user_ids = self._safe_string_tuple_allow_empty(
            value.get("allow_user_ids", [])
        )
        allow_user_groups = self._safe_string_tuple_allow_empty(
            value.get("allow_user_groups", [])
        )
        if allow_user_ids is None or allow_user_groups is None:
            return None
        # group 名写错会让放量范围不可信；这里禁用整份配置，
        # 不做“部分 rule 生效”的容错。
        if any(group_name not in user_groups for group_name in allow_user_groups):
            return None
        if not allow_all_users and not allow_user_ids and not allow_user_groups:
            return None

        return ArcaBotCreateBaasRolloutScope(
            allow_all_users=allow_all_users,
            allow_user_groups=allow_user_groups,
            allow_user_ids=allow_user_ids,
        )

    def _safe_string(
        self,
        value: Any,
        *,
        allowed_values: set[str] | None = None,
    ) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if allowed_values is not None and normalized not in allowed_values:
            return None
        return normalized

    def _safe_string_tuple_allow_empty(self, value: Any) -> tuple[str, ...] | None:
        if not isinstance(value, list) or len(value) > self.MAX_LIST_SIZE:
            return None

        result: list[str] = []
        for item in value:
            normalized = self._safe_string(item)
            if normalized is None:
                return None
            result.append(normalized)
        return tuple(result)
