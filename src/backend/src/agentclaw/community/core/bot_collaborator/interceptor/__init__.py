"""拦截器模块。

提供 API 拦截器机制，用于请求预处理和权限检查。

主要组件：
- InterceptorContext: 拦截器上下文
- Interceptor: 拦截器协议
- InterceptedResponse: 拦截器中断响应
- with_interceptors: 拦截器装饰器
- PermissionParams: 权限参数
- PermissionParamsExtractor: 权限参数提取器协议
- SimplePermissionParamsExtractor: 简单参数提取器
- FuncPermissionParamsExtractor: 函数式参数提取器
- ExpressionResolver: 表达式解析器
- CollaboratorPermissionInterceptor: 协作者权限检查拦截器
"""
from agentclaw.community.core.bot_collaborator.interceptor.base import (
    InterceptedResponse,
    Interceptor,
    InterceptorContext,
    with_interceptors,
)
from agentclaw.community.core.bot_collaborator.interceptor.expression import (
    ExpressionResolver,
)
from agentclaw.community.core.bot_collaborator.interceptor.extractors import (
    FuncPermissionParamsExtractor,
    PermissionParams,
    PermissionParamsExtractor,
    ParamsExtractorFunc,
    SimplePermissionParamsExtractor,
)
from agentclaw.community.core.bot_collaborator.interceptor.collaborator import (
    CollaboratorPermissionInterceptor,
)

__all__ = [
    # 基础组件
    "InterceptorContext",
    "Interceptor",
    "InterceptedResponse",
    "with_interceptors",
    # 表达式解析
    "ExpressionResolver",
    # 权限参数提取
    "PermissionParams",
    "PermissionParamsExtractor",
    "ParamsExtractorFunc",
    "SimplePermissionParamsExtractor",
    "FuncPermissionParamsExtractor",
    # 具体拦截器
    "CollaboratorPermissionInterceptor",
]