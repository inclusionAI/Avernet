"""Skills Pool 首次迁移认领的环境隔离灰度门禁。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from injector import inject

from agentclaw.community.core.common_config.service import CommonConfigService
from agentclaw.community.core.common_config.whitelist_service import (
    CommonWhiteListService,
)
from agentclaw.community.core.skills_pool.types import RolloutEvidence
from agentclaw.community.log import get_logger

logger = get_logger()

SKILLS_POOL_ROLLOUT_BUSINESS_CODE = "skills_pool"
SKILLS_POOL_ROLLOUT_PARAM_CODE = "layout_rollout"
POOL_FILE_ENGINES = frozenset({"openclaw", "claude_code", "aicoding", "hermes"})


class BotRuntimeForm(StrEnum):
    """迁移入口看到的 Bot 运行形态。"""

    PERSONAL = "personal"
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
    ENABLE_ALL_FORBIDDEN = "enable_all_forbidden"
    ENGINE_NOT_SUPPORTED = "engine_not_supported"
    ENGINE_NOT_PROMOTED = "engine_not_promoted"
    BOT_NOT_WHITELISTED = "bot_not_whitelisted"
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

    @staticmethod
    def _valid_identity(value: Any) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, (str, int))
            and str(value).strip() not in {"", "*"}
        )

    @classmethod
    def _valid_config_value(cls, value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        if value.get("enable_all") is not False:
            return value.get("enable_all") is True

        engines = value.get("promoted_engines")
        whitelist = value.get("whitelist")
        if not isinstance(engines, list) or not isinstance(whitelist, list):
            return False
        if any(not isinstance(engine, str) or not engine for engine in engines):
            return False
        allowed_entry_keys = {"owner_id", "bot_id", "batch_id"}
        for entry in whitelist:
            if not isinstance(entry, dict):
                return False
            if not set(entry).issubset(allowed_entry_keys):
                return False
            if not cls._valid_identity(entry.get("owner_id")):
                return False
            if not cls._valid_identity(entry.get("bot_id")):
                return False
            batch_id = entry.get("batch_id")
            if batch_id is not None and not cls._valid_identity(batch_id):
                return False
        return True

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
        config_version = config.get("gmt_modified")
        value = config.get("param_value")
        if (
            isinstance(config_id, bool)
            or not isinstance(config_id, int)
            or not isinstance(config_version, str)
            or not config_version
            or not self._valid_config_value(value)
        ):
            return self._reject(RolloutDecisionReason.CONFIG_INVALID)
        assert isinstance(value, dict)

        if value.get("enable_all") is True:
            return self._reject(RolloutDecisionReason.ENABLE_ALL_FORBIDDEN)
        promoted_engines = value["promoted_engines"]
        if engine_type not in promoted_engines:
            return self._reject(RolloutDecisionReason.ENGINE_NOT_PROMOTED)

        whitelist = value["whitelist"]
        entry = self._whitelist_service.find_bot_whitelist_entry(
            whitelist,
            owner_id=owner_id,
            bot_id=bot_id,
        )
        if entry is None:
            return self._reject(RolloutDecisionReason.BOT_NOT_WHITELISTED)

        batch_id = entry.get("batch_id")
        evidence = RolloutEvidence(
            env=env,
            config_id=config_id,
            config_version=config_version,
            batch_id=str(batch_id) if batch_id is not None else None,
            engine_type=engine_type,
            decision_reason=RolloutDecisionReason.ELIGIBLE.value,
        )
        return RolloutDecision(
            eligible=True,
            reason=RolloutDecisionReason.ELIGIBLE,
            evidence=evidence,
        )
