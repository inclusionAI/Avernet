"""评测绑定查询逻辑 — 从 _bot_binding_resolver 抽取。

统一的评测环境 binding 查询入口，供 BotBindingResolver 和
其他消费者通过 EvalBindingResolverProtocol Plugin 调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from secbaas.community.api.eval_env._protocols import EvalBindingResolverProtocol

# default_tag 键名（与 OCB 侧 device_props 中的键一致）
DYNAMIC_ENV_TAG_KEY = "AGENTCLAW_DEFAULT_TAG"


def resolve_eval_binding_id(
    eval_binding_resolver: "EvalBindingResolverProtocol",
    *,
    bot_id: str,
    entity_id: str,
    env: str,
) -> int | None:
    """解析评测环境的 binding_id。

    委托 EvalBindingResolverProtocol Plugin 处理。
    当 Plugin 为 stub 实现时返回 None，降级走生产。

    Parameters
    ----------
    eval_binding_resolver : EvalBindingResolverProtocol
        评测绑定解析 Plugin。
    bot_id : str
        Bot 标识。
    entity_id : str
        实体标识。
    env : str
        环境标识。

    Returns
    -------
    int | None
        评测 binding_id，降级时返回 None。
    """
    return eval_binding_resolver.resolve_eval_binding(
        bot_id=bot_id,
        entity_id=entity_id,
        env=env,
    )


def is_eval_env_enabled(
    eval_binding_resolver: "EvalBindingResolverProtocol",
) -> bool:
    """检查评测环境功能是否启用。

    委托 EvalBindingResolverProtocol Plugin 处理。

    Parameters
    ----------
    eval_binding_resolver : EvalBindingResolverProtocol
        评测绑定解析 Plugin。

    Returns
    -------
    bool
        启用返回 True，否则 False。
    """
    return eval_binding_resolver.is_eval_env_enabled()