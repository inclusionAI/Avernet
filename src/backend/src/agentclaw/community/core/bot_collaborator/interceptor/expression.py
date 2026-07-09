"""表达式解析器。

支持从 route_kwargs 中提取参数值，使用 $ 前缀表达式。
"""
from __future__ import annotations

from typing import Any


class ExpressionResolver:
    """表达式解析器。

    支持的表达式格式：
    - "$request.publish_id"        → route_kwargs["request"].publish_id
    - "$request.bot.id"            → route_kwargs["request"].bot.id
    - 非 $ 开头的字符串             → 直接返回原值（字面值）
    """

    def resolve(self, expr: str, route_kwargs: dict) -> Any:
        """解析表达式并返回值。

        Args:
            expr: 表达式字符串，如 "$request.publish_id" 或字面值
            route_kwargs: 路由参数字典

        Returns:
            解析后的值，或 None 如果无法解析
        """
        if not isinstance(expr, str):
            return expr

        # 非 $ 开头，当作字面值返回
        if not expr.startswith("$"):
            return expr

        # 去掉 $ 前缀，获取路径
        path = expr[1:]
        if not path:
            return None

        # 点分隔路径导航
        parts = path.split(".")

        # 获取第一层
        obj = route_kwargs.get(parts[0])
        if obj is None:
            return None

        # 导航剩余路径
        for part in parts[1:]:
            if obj is None:
                return None

            if hasattr(obj, part):
                obj = getattr(obj, part)
            elif isinstance(obj, dict):
                obj = obj.get(part)
            else:
                return None

        return obj
