"""Pre-only fixed Bot discover for the candidate-search experiment."""

from __future__ import annotations

import logging
from typing import Any

from agentclaw.community.core.task.task_runner.client.fixed_candidate_bot_discover import (
    FixedCandidateBotDiscover,
)
from agentclaw.community.utils.env_utils import get_current_env

logger = logging.getLogger("task.fixed_search")


_FIXED_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "bot_id": "20261020_xa4pi64f:146836",
        "bot_name": "RiskProfilePlus-任务分身",
        "bot_desc": "RP+平台：securitydataservice 取数、ODPS 回流、View 构建、traceId 诊断",
        "recommend": {"score": 1.0},
    },
    {
        "bot_id": "20261014_dpv2dfuk:35983",
        "bot_name": "UCT20值班助手",
        "bot_desc": "策略配置 / 切流评估 / Trace 耗时 / 策略下发链路 / 下发链路监控",
        "recommend": {"score": 1.0},
    },
    {
        "bot_id": "20261014_im6mcv40:35983",
        "bot_name": "aliuct值班助手",
        "bot_desc": "UCT1.0 / aliuct：策略拦截 / 超时 / 未命中 / 引擎答疑 / 策略仿真",
        "recommend": {"score": 1.0},
    },
    {
        "bot_id": "20261014_fnr8p3xe:35983",
        "bot_name": "FeaturePro",
        "bot_desc": "FeaturePro 平台：特征创建/迁移/验证/报错诊断",
        "recommend": {"score": 1.0},
    },
)


def fixed_pre_candidate_search() -> FixedCandidateBotDiscover | None:
    """Return the fixed pre-only discover, or keep semantic search elsewhere."""
    if get_current_env() != "pre":
        return None
    logger.info(
        "[task][fixed-search] pre fixed search enabled candidates=%s",
        [item.get("bot_id") for item in _FIXED_CANDIDATES],
    )
    return FixedCandidateBotDiscover(_FIXED_CANDIDATES)


__all__ = ["fixed_pre_candidate_search"]
