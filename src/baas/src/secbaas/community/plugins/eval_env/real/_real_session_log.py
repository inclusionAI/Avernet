"""RealEvalSessionLog — 评测会话日志 Real 实现。

从 ``_runner`` 和 ``_bot_run_utils`` 中的
eval 观测字段逻辑迁移。
"""

from __future__ import annotations

from typing import Any

from secbaas.community.logger import get_logger
from secbaas.community.spi.eval_env import EvalSessionLog

logger = get_logger("core-bot-run")


class RealEvalSessionLog(EvalSessionLog):
    """评测会话日志的 Real 实现。

    记录评测会话日志并向 metadata 注入评测观测字段。
    """

    def log_eval_session(
        self,
        *,
        eval_id: str,
        bot_id: str,
        session_id: str,
        method: str,
    ) -> None:
        """记录评测会话日志。"""
        logger.info(
            "[EvalSessionLog] eval_id=%s, bot_id=%s, session_id=%s, method=%s",
            eval_id,
            bot_id,
            session_id,
            method,
        )

    def enrich_chat_metadata(
        self,
        *,
        metadata: dict[str, Any],
        run_id: str,
    ) -> dict[str, Any]:
        """向会话 metadata 注入评测观测字段。"""
        # 原逻辑：从 metadata 中提取 eval_id / default_tag
        # 并注入观测字段。此处委托处理。
        eval_id = metadata.get("eval_id")
        default_tag = metadata.get("default_tag")

        if eval_id or default_tag:
            metadata["eval_observed"] = True
            metadata["eval_run_id"] = run_id

        return metadata

    def extract_eval_headers(
        self,
        *,
        metadata: dict[str, Any],
        x_eval_id: str | None,
        x_default_tag: str | None,
    ) -> dict[str, Any]:
        """从 HTTP Header 提取评测标识并注入 metadata。"""
        if x_eval_id:
            metadata["eval_id"] = x_eval_id
            if not x_eval_id.startswith("eval"):
                logger.warning(
                    "[EvalSessionLog] x-eval-id format mismatch: %s, "
                    "expected 'eval-...' format",
                    x_eval_id,
                )
        if x_default_tag:
            metadata["default_tag"] = x_default_tag
            metadata.setdefault("bot_options", {})["lifecycle_stage"] = x_default_tag
        return metadata
