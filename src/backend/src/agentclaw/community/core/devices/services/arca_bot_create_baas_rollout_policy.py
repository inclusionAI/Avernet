"""原 ARCA 创建分支切 BaaS 的创建期灰度策略。"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.bot_management.engines.aicoding import (
    CODING_TEMPLATE_TYPES as AICODING_TEMPLATE_TYPES,
)
from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_branches import (
    SUPPORTED_ARCA_CREATE_BAAS_ROLLOUT_BRANCHES,
)
from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_config import (
    ArcaBotCreateBaasRolloutConfig,
    ArcaBotCreateBaasRolloutConfigProvider,
    ArcaBotCreateBaasRolloutRule,
)
from agentclaw.community.core.devices.services.device_service import (
    ARCA_DEVICE_PROVIDER,
    BAAS_DEVICE_PROVIDER,
)
from agentclaw.community.log import get_logger


logger = get_logger()


@dataclass(frozen=True)
class ArcaBotCreateBaasRolloutDecision:
    target_provider: str
    reason: str
    rollout_version: str = ""
    engine_bucket: str = "openclaw"


class ArcaBotCreateBaasRolloutPolicy:
    """决定原 ARCA 创建分支是否切到 BaaS。

    这不是通用 provider selector，只服务历史上默认创建 ARCA 容器、
    本期要灰度迁到 BaaS 的分支。新 BaaS-native 分支应显式传入
    ``device_provider``，绕过本策略。
    """

    CODING_TEMPLATE_TYPES = AICODING_TEMPLATE_TYPES
    LEGACY_ARCA_CREATE_BRANCHES = SUPPORTED_ARCA_CREATE_BAAS_ROLLOUT_BRANCHES

    def __init__(
        self,
        config_provider: ArcaBotCreateBaasRolloutConfigProvider | None = None,
    ) -> None:
        self._config_provider = (
            config_provider or ArcaBotCreateBaasRolloutConfigProvider()
        )

    def decide(
        self,
        *,
        user_id: str,
        bot_type: str,
        engine_type: str,
        template_type: str,
    ) -> ArcaBotCreateBaasRolloutDecision:
        engine_bucket = self.normalize_engine_bucket(
            engine_type=engine_type,
            template_type=template_type,
        )
        normalized_bot_type = bot_type.strip().lower()

        # 这里是存量 ARCA 创建分支的 BaaS 灰度，不是新引擎的强制路由。
        # 未分类分支先回退 ARCA，并通过 warning 暴露待归类的接入方。
        if (normalized_bot_type, engine_bucket) not in self.LEGACY_ARCA_CREATE_BRANCHES:
            logger.warning(
                "[arca_to_baas_rollout.decide] unclassified create branch "
                "fallback to arca: user_id=%s, bot_type=%s, engine_bucket=%s, "
                "engine_type=%s, template_type=%s",
                user_id,
                normalized_bot_type,
                engine_bucket,
                engine_type,
                template_type,
            )
            return ArcaBotCreateBaasRolloutDecision(
                target_provider=ARCA_DEVICE_PROVIDER,
                reason="unclassified_branch_fallback",
                engine_bucket=engine_bucket,
            )

        config = self._config_provider.get()

        if not config.enabled:
            decision = ArcaBotCreateBaasRolloutDecision(
                target_provider=ARCA_DEVICE_PROVIDER,
                reason="rollout_disabled",
                engine_bucket=engine_bucket,
            )
            self._log_decision(
                user_id=user_id,
                bot_type=normalized_bot_type,
                engine_bucket=engine_bucket,
                config=config,
                decision=decision,
            )
            return decision

        matched_rule = next(
            (
                rule
                for rule in config.rules
                if rule.bot_type == normalized_bot_type
                and rule.engine_bucket == engine_bucket
            ),
            None,
        )
        if matched_rule is None:
            decision = ArcaBotCreateBaasRolloutDecision(
                target_provider=ARCA_DEVICE_PROVIDER,
                reason="rule_not_found",
                rollout_version=config.version,
                engine_bucket=engine_bucket,
            )
            self._log_decision(
                user_id=user_id,
                bot_type=normalized_bot_type,
                engine_bucket=engine_bucket,
                config=config,
                decision=decision,
            )
            return decision

        if not self._is_user_allowed(
            user_id=user_id,
            config=config,
            rule=matched_rule,
        ):
            decision = ArcaBotCreateBaasRolloutDecision(
                target_provider=ARCA_DEVICE_PROVIDER,
                reason="user_not_allowed",
                rollout_version=config.version,
                engine_bucket=engine_bucket,
            )
            self._log_decision(
                user_id=user_id,
                bot_type=normalized_bot_type,
                engine_bucket=engine_bucket,
                config=config,
                decision=decision,
            )
            return decision

        decision = ArcaBotCreateBaasRolloutDecision(
            target_provider=BAAS_DEVICE_PROVIDER,
            reason="rollout_matched",
            rollout_version=config.version,
            engine_bucket=engine_bucket,
        )
        self._log_decision(
            user_id=user_id,
            bot_type=normalized_bot_type,
            engine_bucket=engine_bucket,
            config=config,
            decision=decision,
        )
        return decision

    @staticmethod
    def _is_user_allowed(
        *,
        user_id: str,
        config: ArcaBotCreateBaasRolloutConfig,
        rule: ArcaBotCreateBaasRolloutRule,
    ) -> bool:
        if rule.allow_all_users:
            return True
        allowed_user_ids = set(rule.allow_user_ids)
        # DRM 解析会校验 group；测试手写 config 时未知 group 按空组处理。
        for group_name in rule.allow_user_groups:
            allowed_user_ids.update(config.user_groups.get(group_name, ()))
        return user_id in allowed_user_ids

    @staticmethod
    def _log_decision(
        *,
        user_id: str,
        bot_type: str,
        engine_bucket: str,
        config: ArcaBotCreateBaasRolloutConfig,
        decision: ArcaBotCreateBaasRolloutDecision,
    ) -> None:
        logger.info(
            f"[arca_to_baas_rollout.decide] user_id={user_id}, "
            f"bot_type={bot_type}, engine_bucket={engine_bucket}, "
            f"enabled={config.enabled}, version={config.version}, "
            f"rule_count={len(config.rules)}, "
            f"target_provider={decision.target_provider}, reason={decision.reason}"
        )

    @classmethod
    def normalize_engine_bucket(
        cls,
        *,
        engine_type: str,
        template_type: str,
    ) -> str:
        normalized_engine = engine_type.strip().lower().replace("-", "_")
        if (
            normalized_engine == "claude_code"
            and template_type in cls.CODING_TEMPLATE_TYPES
        ):
            return "aicoding"
        return normalized_engine
