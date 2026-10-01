"""Skills Pool 首次迁移认领的环境隔离灰度门禁。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from injector import inject

from agentclaw.community.core.common_config.service import CommonConfigService
from agentclaw.community.core.common_config.whitelist_service import (
    CommonWhiteListService,
)
from agentclaw.community.core.skills_pool.types import RolloutEvidence
from agentclaw.community.core.skills_pool.rollout_config import (
    ROLLOUT_SCHEMA_VERSION,
    is_valid_rollout_config_value,
    rollout_schema_version,
)
from agentclaw.community.log import get_logger

logger = get_logger()

SKILLS_POOL_ROLLOUT_BUSINESS_CODE = "skills_pool"
SKILLS_POOL_ROLLOUT_PARAM_CODE = "layout_rollout"
POOL_FILE_ENGINES = frozenset({"openclaw", "claude_code", "aicoding", "hermes"})


class BotRuntimeForm(StrEnum):
    """迁移入口看到的 Bot 运行形态。"""

    PERSONAL = "personal"
    DESKTOP = "desktop"
    SERVICE_DRAFT = "service_draft"
    PUBLISHED_SERVICE = "published_service"


class RolloutDecisionReason(StrEnum):
    """可审计且稳定的门禁判定原因。"""

    ELIGIBLE = "eligible"
    CONFIG_MISSING = "config_missing"
    CONFIG_DISABLED = "config_disabled"
    CONFIG_READ_ERROR = "config_read_error"
    CONFIG_INVALID = "config_invalid"
    CONFIG_ENV_MISMATCH = "config_env_mismatch"
    ENGINE_NOT_SUPPORTED = "engine_not_supported"
    ENGINE_NOT_PROMOTED = "engine_not_promoted"
    ENGINE_ADMISSION_DISABLED = "engine_admission_disabled"
    BOT_NEGATIVE_CONTROL = "bot_negative_control"
    BOT_EXCLUDED = "bot_excluded"
    BOT_NOT_WHITELISTED = "bot_not_whitelisted"
    BOT_NOT_ALLOWED = "bot_not_allowed"
    RUNTIME_NOT_EDITABLE = "runtime_not_editable"


@dataclass(frozen=True, slots=True)
class RolloutDecision:
    """Rollout gate 的结构化结果。"""

    eligible: bool
    reason: RolloutDecisionReason
    evidence: RolloutEvidence | None = None


class SkillsPoolRolloutGate:
    """只决定一个尚处于 Legacy 的 Bot 能否首次认领迁移。"""

    @inject
    def __init__(
        self,
        common_config_service: CommonConfigService,
        whitelist_service: CommonWhiteListService,
    ) -> None:
        self._common_config_service = common_config_service
        self._whitelist_service = whitelist_service

    @staticmethod
    def _reject(reason: RolloutDecisionReason) -> RolloutDecision:
        return RolloutDecision(eligible=False, reason=reason)

    def evaluate(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        engine_type: str,
        runtime_form: BotRuntimeForm,
    ) -> RolloutDecision:
        """按运行形态、engine、环境配置和精确 Bot 身份做 fail-closed 判定。"""

        if engine_type not in POOL_FILE_ENGINES:
            return self._reject(RolloutDecisionReason.ENGINE_NOT_SUPPORTED)
        if (
            runtime_form is not BotRuntimeForm.PERSONAL
            and runtime_form is not BotRuntimeForm.DESKTOP
            and runtime_form is not BotRuntimeForm.SERVICE_DRAFT
        ):
            return self._reject(RolloutDecisionReason.RUNTIME_NOT_EDITABLE)

        try:
            config = self._common_config_service.get_config(
                business_code=SKILLS_POOL_ROLLOUT_BUSINESS_CODE,
                param_code=SKILLS_POOL_ROLLOUT_PARAM_CODE,
                env=env,
                only_enabled=False,
            )
        except Exception:
            logger.exception(
                "[skills_pool.rollout] config read failed env=%s bot_id=%s",
                env,
                bot_id,
            )
            return self._reject(RolloutDecisionReason.CONFIG_READ_ERROR)

        if config is None:
            return self._reject(RolloutDecisionReason.CONFIG_MISSING)
        if config.get("enable") != "1":
            return self._reject(RolloutDecisionReason.CONFIG_DISABLED)
        if config.get("env") != env:
            return self._reject(RolloutDecisionReason.CONFIG_ENV_MISMATCH)

        config_id = config.get("id")
        ext_info = config.get("ext_info")
        revision = ext_info.get("revision") if isinstance(ext_info, dict) else None
        config_version = revision or config.get("gmt_modified")
        value = config.get("param_value")
        if (
            isinstance(config_id, bool)
            or not isinstance(config_id, int)
            or not isinstance(config_version, str)
            or not config_version
            or not is_valid_rollout_config_value(value)
        ):
            return self._reject(RolloutDecisionReason.CONFIG_INVALID)
        assert isinstance(value, dict)

        if rollout_schema_version(value) == ROLLOUT_SCHEMA_VERSION:
            return self._evaluate_v2(
                env=env,
                owner_id=owner_id,
                bot_id=bot_id,
                engine_type=engine_type,
                config_id=config_id,
                config_version=config_version,
                value=value,
            )

        promoted_engines = value["promoted_engines"]
        if engine_type not in promoted_engines:
            return self._reject(RolloutDecisionReason.ENGINE_NOT_PROMOTED)

        negative_control = self._whitelist_service.find_bot_whitelist_entry(
            value.get("negative_controls", []),
            owner_id=owner_id,
            bot_id=bot_id,
        )
        if negative_control is not None:
            return self._reject(RolloutDecisionReason.BOT_NEGATIVE_CONTROL)

        batch_id = None
        decision_reason = "environment_full_rollout"
        owner_full_rollout = any(
            str(entry["owner_id"]) == str(owner_id) and entry["engine"] == engine_type
            for entry in value.get("full_rollout_owners", [])
        )
        if (
            value["enable_all"] is False
            and engine_type not in value.get("full_rollout_engines", [])
            and not owner_full_rollout
        ):
            whitelist = value["whitelist"]
            entry = self._whitelist_service.find_bot_whitelist_entry(
                whitelist,
                owner_id=owner_id,
                bot_id=bot_id,
            )
            if entry is None:
                return self._reject(RolloutDecisionReason.BOT_NOT_WHITELISTED)
            batch_id = entry.get("batch_id")
            decision_reason = "exact_bot_whitelist"
        elif value["enable_all"] is False and owner_full_rollout:
            decision_reason = "owner_full_rollout"
        elif value["enable_all"] is False:
            decision_reason = "engine_full_rollout"
        evidence = RolloutEvidence(
            env=env,
            config_id=config_id,
            config_version=config_version,
            batch_id=str(batch_id) if batch_id is not None else None,
            engine_type=engine_type,
            decision_reason=decision_reason,
        )
        return RolloutDecision(
            eligible=True,
            reason=RolloutDecisionReason.ELIGIBLE,
            evidence=evidence,
        )

    def _evaluate_v2(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        engine_type: str,
        config_id: int,
        config_version: str,
        value: dict[str, object],
    ) -> RolloutDecision:
        admission = value["engine_admission"]
        assert isinstance(admission, dict)
        if admission.get(engine_type) is not True:
            return self._reject(RolloutDecisionReason.ENGINE_ADMISSION_DISABLED)

        exclusions = value["bot_exclusions"]
        assert isinstance(exclusions, list)
        if self._matches_bot_rule(
            exclusions,
            owner_id=owner_id,
            bot_id=bot_id,
            engine=engine_type,
        ):
            return self._reject(RolloutDecisionReason.BOT_EXCLUDED)

        allowlist = value["bot_allowlist"]
        assert isinstance(allowlist, list)
        decision_reason: str | None = None
        if self._matches_bot_rule(
            allowlist,
            owner_id=owner_id,
            bot_id=bot_id,
            engine=engine_type,
        ):
            decision_reason = "exact_bot_allowlist"
        else:
            owner_rollouts = value["owner_rollouts"]
            assert isinstance(owner_rollouts, list)
            if any(
                str(entry["owner_id"]) == str(owner_id)
                and entry["engine"] == engine_type
                for entry in owner_rollouts
            ):
                decision_reason = "owner_rollout"
            else:
                environment_rollouts = value["environment_rollouts"]
                assert isinstance(environment_rollouts, list)
                if engine_type in environment_rollouts:
                    decision_reason = "environment_rollout"
        if decision_reason is None:
            return self._reject(RolloutDecisionReason.BOT_NOT_ALLOWED)

        return RolloutDecision(
            eligible=True,
            reason=RolloutDecisionReason.ELIGIBLE,
            evidence=RolloutEvidence(
                env=env,
                config_id=config_id,
                config_version=config_version,
                batch_id=None,
                engine_type=engine_type,
                decision_reason=decision_reason,
            ),
        )

    @staticmethod
    def _matches_bot_rule(
        rules: list[object],
        *,
        owner_id: str,
        bot_id: str,
        engine: str,
    ) -> bool:
        return any(
            isinstance(entry, dict)
            and str(entry.get("owner_id")) == str(owner_id)
            and str(entry.get("bot_id")) == str(bot_id)
            and entry.get("engine") == engine
            for entry in rules
        )
