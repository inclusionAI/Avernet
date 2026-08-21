"""NoopEvalSessionLog — 评测会话日志 Stub 实现。

评测功能关闭时不记录日志，不注入观测字段。
"""

from __future__ import annotations

from typing import Any

from secbaas.community.api.eval_env import EvalSessionLogProtocol


class NoopEvalSessionLog(EvalSessionLogProtocol):
    """评测会话日志的 Stub 实现。

    所有方法为空操作，不记录日志也不注入观测字段。
    """

    def log_eval_session(
        self,
        *,
        eval_id: str,
        bot_id: str,
        session_id: str,
        method: str,
    ) -> None:
        """Stub：空操作。"""

    def enrich_chat_metadata(
        self,
        *,
        metadata: dict[str, Any],
        run_id: str,
    ) -> dict[str, Any]:
        """Stub：返回原 metadata。"""
        return metadata

    def extract_eval_headers(
        self,
        *,
        metadata: dict[str, Any],
        x_eval_id: str | None,
        x_default_tag: str | None,
    ) -> dict[str, Any]:
        """Stub：返回原 metadata。"""
        return metadata
