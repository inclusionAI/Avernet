"""评测环境 API — 数据模型与业务常量。

Protocol 定义在 spi/eval_env，service 直接依赖 spi，
api 层只保留数据模型和常量。
"""

from ._models import EvalBindingInfo

# default_tag 键名（与 OCB 侧 device_props 中的键一致）
DYNAMIC_ENV_TAG_KEY = "AGENTCLAW_DEFAULT_TAG"

__all__ = [
    "DYNAMIC_ENV_TAG_KEY",
    "EvalBindingInfo",
]