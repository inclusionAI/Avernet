"""评测绑定查询 — 从 _bot_binding_resolver 抽取的独立模块。

将评测环境 binding 解析逻辑从 BotBindingResolver 中分离，
委托给 EvalBindingResolverProtocol Plugin 处理。
"""

from ._eval_binding_query import (
    DYNAMIC_ENV_TAG_KEY,
    is_eval_env_enabled,
    resolve_eval_binding_id,
)

__all__ = [
    "DYNAMIC_ENV_TAG_KEY",
    "resolve_eval_binding_id",
    "is_eval_env_enabled",
]
