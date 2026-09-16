"""aicoding 引擎特有：是否为 bot 创建 hosted DIMA workspace 的开关判定。

dima_workspace 是 aicoding 引擎 bot 的能力开关，仅对 aicoding engine 有意义；
故本模块归在 ``services/aicoding`` 下，其它引擎不应依赖该开关。

读取 template_config.bot_template_config.capabilities.dima_workspace：
``True`` 或字符串 ``"true"``（忽略大小写/前后空白）视为开启，其它取值
（含 ``False``、``"false"``、缺失）视为关闭。
"""
from __future__ import annotations

from typing import Any, Mapping


def _is_dima_workspace_truthy(value: Any) -> bool:
    """dima_workspace 开关是否打开：``True`` 或 ``"true"``。"""
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() == "true":
        return True
    return False


def has_dima_workspace_enabled(template_config: Any) -> bool:
    """aicoding bot 的 template_config 是否开启 dima_workspace（路径 bot_template_config.capabilities.dima_workspace）。"""
    if not isinstance(template_config, Mapping):
        return False
    bot_template_config = template_config.get("bot_template_config")
    if not isinstance(bot_template_config, Mapping):
        return False
    capabilities = bot_template_config.get("capabilities")
    if not isinstance(capabilities, Mapping):
        return False
    return _is_dima_workspace_truthy(capabilities.get("dima_workspace"))
