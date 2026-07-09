"""拦截器基础设施。

提供拦截器协议、上下文和装饰器实现。
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Protocol, runtime_checkable

from fastapi import Request
from fastapi.responses import JSONResponse
from injector import Injector

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.auth import AuthenticatedIdentity


@dataclass
class InterceptedResponse:
    """拦截器中断响应。

    当拦截器需要中断请求时，设置此响应对象。
    """
    success: bool
    message: str
    error_code: int = 403
    data: Any | None = None


@dataclass
class InterceptorContext:
    """拦截器执行上下文。

    封装了请求处理过程中的所有上下文信息，供拦截器使用。

    Attributes:
        func: 被拦截的路由函数
        user: 当前用户信息
        route_kwargs: 路由函数关键字参数（包含 Pydantic 模型入参）
        response: 拦截器可设置的响应对象，用于中断请求
        metadata: 自定义元数据，用于拦截器之间传递信息
        result: 业务执行结果（仅 after 阶段可用）
        injector: FastAPI-attached DI injector. Available at request
            time (set by ``with_interceptors`` from
            ``request.app.state.injector``). Interceptors use this
            instead of reaching for the legacy module global —
            interceptors are cross-cutting infrastructure that runs at
            decoration time (before the injector exists), so they
            cannot receive deps via constructor injection. This
            ``request.app.state.injector`` boundary is the FastAPI-
            sanctioned path that ``attach_injector(app, injector)``
            sets up at the composition root.
    """
    func: Callable | None = None
    user: AuthenticatedIdentity | None = None
    route_kwargs: dict = field(default_factory=dict)
    response: InterceptedResponse | dict | None = None
    metadata: dict = field(default_factory=dict)
    result: Any | None = None
    injector: Injector | None = None


@runtime_checkable
class Interceptor(Protocol):
    """拦截器协议。

    所有拦截器必须实现此协议。拦截器可以在请求处理前后执行自定义逻辑，
    并可通过返回 None 来中断请求处理。
    """

    name: str
    """拦截器名称，用于日志和调试"""

    async def before(self, ctx: InterceptorContext) -> InterceptorContext | None:
        """前置拦截。

        在路由处理函数执行前调用。

        Args:
            ctx: 拦截器上下文

        Returns:
            - 返回修改后的 ctx：继续执行后续拦截器和路由处理
            - 返回 None：中断请求，应设置 ctx.response 作为响应
        """
        ...

    async def after(self, ctx: InterceptorContext) -> None:
        """后置拦截。

        在路由处理函数执行后调用（仅当前置拦截全部通过时）。
        用于记录日志等不影响主流程的操作。
        异常会被捕获并忽略，不会影响业务响应。

        Args:
            ctx: 拦截器上下文，ctx.result 包含业务返回值
        """
        ...


def with_interceptors(
    *interceptors: Interceptor,
    preload_services: list[type] | None = None,
):
    """拦截器装饰器工厂。

    支持前置拦截和后置拦截，无需修改原有路由函数参数定义。

    Important:
        被装饰的路由函数**必须**包含 `user: AuthenticatedIdentity = Depends(get_current_user)` 参数。

    Args:
        *interceptors: 拦截器实例列表
        preload_services: （可选）需要预加载的服务类列表，服务实例会被放入 ctx.metadata。
            注意：拦截器内部通常直接使用全局注入器获取服务，此参数仅用于特殊场景。

    Returns:
        装饰器函数

    Example:
        @with_interceptors(
            CollaboratorPermissionInterceptor(
                required_level=PermissionLevel.ADMIN,
                params_extractor=extract_from_publish_id,
                extractor_params={"publish_id": "$request.publish_id"},
            ),
        )
        async def my_route(
            request: MyRequest,  # Pydantic 模型，通过 $request.xxx 访问
            user: AuthenticatedIdentity = Depends(get_current_user),
        ):
            ...
    """
    # Reserved kwarg name used to smuggle the FastAPI ``Request`` into
    # the wrapper without forcing every route to add a ``request: Request``
    # parameter. The wrapper pops it before delegating to the underlying
    # route handler so the handler signature stays unchanged.
    _REQUEST_KWARG = "__interceptor_request__"

    def decorator(func):
        orig_sig = inspect.signature(func)
        # If the underlying route doesn't already declare a Request param,
        # splice one in on the wrapper. FastAPI sees the wrapper's signature
        # at registration time and supplies the Request at request time.
        already_has_request = any(
            param.annotation is Request for param in orig_sig.parameters.values()
        )

        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Capture the Request — either passed through under our reserved
            # kwarg name (signature was spliced) or already a param the
            # handler itself declared.
            request: Request | None = kwargs.pop(_REQUEST_KWARG, None)
            if request is None:
                for value in kwargs.values():
                    if isinstance(value, Request):
                        request = value
                        break

            # 从 kwargs 中提取 user（支持多种来源）
            user: AuthenticatedIdentity | None = None

            for value in kwargs.values():
                if isinstance(value, AuthenticatedIdentity):
                    user = value
                    break
                elif isinstance(value, AuthenticatedUser):
                    # AuthenticatedUser needs conversion to AuthenticatedIdentity for ctx.user
                    user = AuthenticatedIdentity(
                        id=value.id,
                        staffId=value.staffId,
                        operatorName=value.operatorName,
                        outUserNo=value.id,
                        nickName=value.nickName or value.staffId,
                    )
                    break
                # 如果有 RequestContext，从中提取 user_id 创建 AuthenticatedIdentity
                elif hasattr(value, 'user_id') and user is None:
                    user = AuthenticatedIdentity(
                        id=value.user_id,
                        staffId=value.user_id,
                        operatorName=value.user_id,
                        outUserNo=value.user_id,
                        nickName=getattr(value, 'nick_name', value.user_id),
                    )

            # Resolve the DI injector from the FastAPI app state. Set
            # by ``attach_injector(app, injector)`` at the composition
            # root (api/app.py). Interceptors use ``ctx.injector`` to
            # resolve services they need at request time.
            injector: Injector | None = None
            if request is not None:
                injector = getattr(request.app.state, "injector", None)

            # 构建拦截器上下文
            ctx = InterceptorContext(
                func=func,
                user=user,
                route_kwargs=kwargs,
                injector=injector,
            )

            # 预加载服务：从 ctx.injector 获取
            if preload_services and injector is not None:
                for service_cls in preload_services:
                    try:
                        service = injector.get(service_cls)
                        key = service_cls.__name__
                        key = "".join(
                            ["_" + c.lower() if c.isupper() else c for c in key]
                        ).lstrip("_")
                        ctx.metadata[key] = service
                    except Exception:
                        pass

            # 执行前置拦截器链
            for interceptor in interceptors:
                result = await interceptor.before(ctx)
                if result is None:
                    if ctx.response:
                        if isinstance(ctx.response, InterceptedResponse):
                            return JSONResponse(
                                status_code=ctx.response.error_code,
                                content={
                                    "success": ctx.response.success,
                                    "message": ctx.response.message,
                                    "error_code": ctx.response.error_code,
                                    "data": ctx.response.data,
                                }
                            )
                        if isinstance(ctx.response, dict):
                            return JSONResponse(
                                status_code=ctx.response.get("error_code", 403),
                                content=ctx.response,
                            )
                        return ctx.response
                    return JSONResponse(
                        status_code=403,
                        content={"success": False, "message": "权限不足"}
                    )
                ctx = result

            # 执行原函数
            try:
                ctx.result = await func(*args, **kwargs)
                return ctx.result
            finally:
                # 执行后置拦截器链（忽略异常，不影响主流程）
                for interceptor in interceptors:
                    try:
                        await interceptor.after(ctx)
                    except Exception:
                        pass

        # Build the wrapper's signature: route's own params, plus a
        # spliced-in Request kwarg if the route didn't already declare
        # one. FastAPI uses ``wrapper.__signature__`` for dependency
        # resolution, so adding the Request here is enough to make
        # FastAPI supply it at request time without touching any
        # of the existing route declarations.
        if already_has_request:
            wrapper.__signature__ = orig_sig  # type: ignore
        else:
            extra = inspect.Parameter(
                _REQUEST_KWARG,
                kind=inspect.Parameter.KEYWORD_ONLY,
                annotation=Request,
            )
            new_params = list(orig_sig.parameters.values()) + [extra]
            wrapper.__signature__ = orig_sig.replace(parameters=new_params)  # type: ignore

        return wrapper
    return decorator
