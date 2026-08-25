"""NoopEvalConsistencyCheck — 评测一致性检查 Stub 实现。

评测功能关闭时跳过一致性检查，始终返回 True。
"""

from __future__ import annotations

from typing import Any

from secbaas.community.spi.eval_env import EvalConsistencyCheckProtocol


class NoopEvalConsistencyCheck(EvalConsistencyCheckProtocol):
    """评测一致性检查的 Stub 实现。

    始终返回 ``True``，等效于跳过一致性检查。
    """

    def check_default_tag_consistency(
        self,
        *,
        binding_info: Any,
        chat_metadata: dict[str, Any],
    ) -> bool:
        """Stub：始终返回 True，跳过检查。"""
        return True
