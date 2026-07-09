"""权限参数提取器。

提供从请求上下文中提取 bot_id 和 owner_id 的能力。
支持表达式语法和自定义异步函数提取。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Awaitable, Protocol, runtime_checkable

from agentclaw.community.core.bot_collaborator.interceptor.base import InterceptorContext
from agentclaw.community.core.bot_collaborator.interceptor.expression import ExpressionResolver


@dataclass
class PermissionParams:
    """权限检查所需参数。"""
    bot_id: str | None = None
    owner_id: str | None = None


@runtime_checkable
class PermissionParamsExtractor(Protocol):
    """权限参数提取器协议。

    一次提取 bot_id 和 owner_id，适用于需要从数据库查询的场景。
    """

    async def extract(self, ctx: InterceptorContext) -> PermissionParams:
        """提取权限参数。

        Args:
            ctx: 拦截器上下文

        Returns:
            PermissionParams 包含 bot_id 和 owner_id
        """
        ...


# 类型别名：支持同步函数或异步函数
ParamsExtractorFunc = (
    Callable[[InterceptorContext], PermissionParams] |
    Callable[[InterceptorContext], Awaitable[PermissionParams]]
)


class SimplePermissionParamsExtractor:
    """简单参数提取器。

    从 route_kwargs 直接获取参数值。
    支持表达式语法：$request.xxx 表示 route_kwargs["request"].xxx
    """

    def __init__(
        self,
        bot_id: str = "bot_id",
        owner_id: str = "owner_id",
    ):
        """初始化提取器。

        Args:
            bot_id: bot_id 参数表达式，默认 "bot_id"
                表达式语法："$request.bot_id" 表示 route_kwargs["request"].bot_id
            owner_id: owner_id 参数表达式，默认 "owner_id"
        """
        self.bot_id_expr = bot_id
        self.owner_id_expr = owner_id
        self.resolver = ExpressionResolver()

    async def extract(self, ctx: InterceptorContext) -> PermissionParams:
        """从 route_kwargs 提取参数。"""
        bot_id = self._get_param(ctx, self.bot_id_expr)
        owner_id = self._get_param(ctx, self.owner_id_expr)
        return PermissionParams(bot_id=bot_id, owner_id=owner_id)

    def _get_param(self, ctx: InterceptorContext, expr: str) -> str | None:
        """获取参数值。

        Args:
            ctx: 拦截器上下文
            expr: 表达式或参数名

        Returns:
            参数值或 None
        """
        value = self.resolver.resolve(expr, ctx.route_kwargs)
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return str(value)


class FuncPermissionParamsExtractor:
    """函数式参数提取器。

    支持自定义异步函数，一次查询获取两个参数。
    适用于需要从数据库查询的场景。
    """

    def __init__(self, extract_func: ParamsExtractorFunc):
        """初始化提取器。

        Args:
            extract_func: 提取函数，接收 InterceptorContext，返回 PermissionParams
        """
        self.extract_func = extract_func

    async def extract(self, ctx: InterceptorContext) -> PermissionParams:
        """执行提取函数。"""
        result = self.extract_func(ctx)
        if asyncio.iscoroutine(result):
            return await result
        return result
