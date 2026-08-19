"""RealEvalConsistencyCheck — 评测一致性检查 Real 实现。

从 ``_baas_service`` 中的 eval 一致性检查逻辑迁移。
"""

from __future__ import annotations

from typing import Any

from secbaas.community.api.eval_env._protocols import EvalConsistencyCheckProtocol
from secbaas.community.core.service.eval_binding import DYNAMIC_ENV_TAG_KEY
from secbaas.community.logger import get_logger

logger = get_logger("core-bot-run")


class RealEvalConsistencyCheck(EvalConsistencyCheckProtocol):
    """评测一致性检查的 Real 实现。

    检查 binding_info 中的 default_tag 与 chat_metadata 中的
    default_tag 是否一致，防止评测流量误路由。
    """

    def check_default_tag_consistency(
        self,
        *,
        binding_info: Any,
        chat_metadata: dict[str, Any],
    ) -> bool:
        """检查 default_tag 一致性。"""
        binding_tag = None
        metadata_tag = chat_metadata.get("default_tag")

        if binding_info is not None:
            device_props = getattr(binding_info, "device_props", None)
            if device_props:
                binding_tag = device_props.get(DYNAMIC_ENV_TAG_KEY)

        if not metadata_tag:
            # 无 metadata tag 时不需要一致性检查
            return True

        if binding_tag and binding_tag != metadata_tag:
            logger.warning(
                "[RealEvalConsistencyCheck] default_tag 不一致: "
                "binding_tag=%s, metadata_tag=%s",
                binding_tag,
                metadata_tag,
            )
            return False

        return True