"""aicoding 引擎特有：是否为 bot 创建 hosted workspace 的开关判定。

该开关对应 capabilities.dima_workspace，是 aicoding 引擎 bot 的托管能力，仅对
aicoding engine 有意义；故本模块归在 ``services/aicoding`` 下，其它引擎不应依赖该开关。

读取 template_config 的 capabilities 节点中的 ``dima_workspace``：
``True`` 或字符串 ``"true"``（忽略大小写/前后空白）视为开启，其它取值
（含 ``False``、``"false"``、缺失）视为关闭。

capabilities 的位置兼容两种形态：

- 扁平 ``template_config.capabilities.dima_workspace`` —— 模板工厂快照的约定
  （见 ``core.bot_management.capabilities``：``capabilities`` 存在时是能力开关的
  唯一事实源），当前全部走这一形态；
- 嵌套 ``template_config.bot_template_config.capabilities.dima_workspace`` ——
  手写配置的历史形态，仅作兼容兜底。
"""
from __future__ import annotations

from typing import Any, Mapping


def _is_dima_workspace_truthy(value: Any) -> bool:
    """workspace 托管开关是否打开：``True`` 或 ``"true"``。"""
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() == "true":
        return True
    return False


def _capabilities_node(template_config: Any) -> Any:
    """取 capabilities 节点：优先根部（工厂快照约定），兜底嵌套历史形态。

    根部按 keys-presence 终态：已声明 ``capabilities``（即使值非 Mapping，如
    None）即为唯一事实源，取值畸形按空配置处理，不回落嵌套形态。
    """
    if not isinstance(template_config, Mapping):
        return None
    if "capabilities" in template_config:
        capabilities = template_config.get("capabilities")
        return capabilities if isinstance(capabilities, Mapping) else {}
    bot_template_config = template_config.get("bot_template_config")
    if isinstance(bot_template_config, Mapping):
        return bot_template_config.get("capabilities")
    return None


def has_dima_workspace_enabled(template_config: Any) -> bool:
    """aicoding bot 是否开启 workspace 托管能力。

    优先读扁平 ``capabilities.dima_workspace``（模板工厂快照约定），
    缺失时兜底读 ``bot_template_config.capabilities.dima_workspace``。
    """
    capabilities = _capabilities_node(template_config)
    if not isinstance(capabilities, Mapping):
        return False
    return _is_dima_workspace_truthy(capabilities.get("dima_workspace"))
