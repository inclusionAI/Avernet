"""Claude Code 引擎 adapter。

薄封装:``ws_path`` 命中 engine 侧 claude_code 专属 router ``/api/claude_code/ws``;
``session_consistency_key`` 返回 ``agent:{tc_bot_id}:session:{run_id}:user:{user_id}`` 作为
device 亲和键;session 创建走基类通用逻辑。经 proxy→沙箱→engine 的现有 WS 通道工作，
adapter 本身不做额外的连通性探活。
"""

from __future__ import annotations

from ..._base import BaseEngineAdapter


class ClaudeCodeAdapter(BaseEngineAdapter):
    """Claude Code 引擎 adapter —— WS 路径 ``/api/claude_code/ws``。"""

    engine_type = "claude_code"
    _WS_PATH = "/api/claude_code/ws"

    def session_consistency_key(
        self,
        *,
        tc_bot_id: str,
        user_id: str,
        run_id: str | None,
        session_id: str | None = None,
    ) -> str | None:
        if session_id is not None:
            return session_id
        return f"agent:{tc_bot_id}:session:{run_id}:user:{user_id}"
