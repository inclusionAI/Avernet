"""
RegexCORSMiddleware — 支持多条正则匹配的 CORS 中间件

继承 FastAPI CORSMiddleware，重写 __call__ 方法，在请求时动态检查正则匹配。
原生 CORSMiddleware 的 allow_origin_regex 仅支持单条正则，
本中间件支持多条正则模式列表，每条独立编译、独立匹配。

与 backend (agentclaw) 保持一致。
"""

import re

from fastapi.middleware.cors import CORSMiddleware


class RegexCORSMiddleware(CORSMiddleware):
    """支持正则匹配的 CORS 中间件

    重写 __call__ 方法，在请求时动态检查正则匹配。
    相比原生 CORSMiddleware 的 allow_origin_regex（仅支持单条正则），
    本中间件支持多条正则模式列表，每条独立编译、独立匹配。

    Args:
        app: ASGI application
        allow_origin_regex: 正则模式列表，每个元素为一条正则字符串。
    """

    def __init__(self, app, allow_origin_regex: list[str] | None = None, **kwargs):
        self._allow_origin_regex = allow_origin_regex or []
        self._compiled_patterns = [re.compile(pattern) for pattern in self._allow_origin_regex]
        super().__init__(app, **kwargs)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from starlette.requests import Request
        request = Request(scope)
        origin = request.headers.get("origin")

        # 如果 origin 匹配正则，临时添加到允许列表
        if origin and self._compiled_patterns:
            if any(pattern.match(origin) for pattern in self._compiled_patterns):
                # 保存原始值
                original_origins = self.allow_origins[:]
                # 添加匹配的 origin
                if origin not in self.allow_origins:
                    self.allow_origins.append(origin)
                # 调用父类处理
                await super().__call__(scope, receive, send)
                # 恢复原始列表（避免累积）
                self.allow_origins = original_origins
                return

        # 否则走默认逻辑
        await super().__call__(scope, receive, send)