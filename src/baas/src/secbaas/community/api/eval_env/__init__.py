"""评测环境 API — 数据模型与能力契约。"""

from ._models import EvalBindingInfo
from ._protocols import (
    EvalBindingResolverProtocol,
    EvalConsistencyCheckProtocol,
    EvalSessionLogProtocol,
)

# default_tag 键名（与 OCB 侧 device_props 中的键一致）
DYNAMIC_ENV_TAG_KEY = "AGENTCLAW_DEFAULT_TAG"

__all__ = [
    "DYNAMIC_ENV_TAG_KEY",
    "EvalBindingInfo",
    "EvalBindingResolverProtocol",
    "EvalConsistencyCheckProtocol",
    "EvalSessionLogProtocol",
]
