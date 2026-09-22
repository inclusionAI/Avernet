"""aicoding 引擎特有：是否为 bot 创建 hosted workspace 的开关判定。

该开关对应 capabilities.dima_workspace，是 aicoding 引擎 bot 的托管能力，仅对
aicoding engine 有意义；故本模块归在 ``services/aicoding`` 下，其它引擎不应依赖该开关。

读取 template_config 的 capabilities 节点中的 ``dima_workspace``：
``True`` 或字符串 ``"true"``（忽略大小写/前后空白）视为开启，其它取值
（含 ``False``、``"false"``、缺失）视为关闭。

capabilities 的位置兼容两种形态（节点归属判定统一复用
``core.bot_management.capabilities.capabilities_node`` 的 key-presence 契约，
不在本模块二次实现）：

- 扁平 ``template_config.capabilities.dima_workspace`` —— 模板工厂快照的约定，
  键存在即为唯一事实源（值畸形按"全部能力关闭"处理，不混入 legacy 数据）；
- 嵌套 ``template_config.bot_template_config.capabilities.dima_workspace`` ——
  手写配置的历史形态，仅当扁平键真正缺失时兜底。
"""
from __future__ import annotations

from typing import Any, Mapping

from ...capabilities import capabilities_node


def _is_dima_workspace_truthy(value: Any) -> bool:
    """workspace 托管开关是否打开：``True`` 或 ``"true"``。"""
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() == "true":
        return True
    return False


def _capabilities_node(template_config: Any) -> Any:
    """取 capabilities 节点：扁平键存在即 terminal，真正缺失才兜底嵌套。"""
    flat = capabilities_node(template_config)
    if flat is not None:
        return flat
    if not isinstance(template_config, Mapping):
        return None
    bot_template_config = template_config.get("bot_template_config")
    if not isinstance(bot_template_config, Mapping):
        return None
    return bot_template_config.get("capabilities")


def has_dima_workspace_enabled(template_config: Any) -> bool:
    """aicoding bot 是否开启 workspace 托管能力。

    优先读扁平 ``capabilities.dima_workspace``（模板工厂快照约定），
    缺失时兜底读 ``bot_template_config.capabilities.dima_workspace``。
    """
    capabilities = _capabilities_node(template_config)
    if not isinstance(capabilities, Mapping):
        return False
    return _is_dima_workspace_truthy(capabilities.get("dima_workspace"))