"""
请求上下文管理

使用 contextvars 在异步调用链中隐式传递请求上下文（如 Cookie），
避免将 Cookie 作为参数层层传递。
"""

import contextvars
import os
from typing import Optional

# 线程/协程隔离的全局变量：当前请求中的 Cookie
current_cookie: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "cookie", default=None
)


def get_current_cookie(fallback_to_env: bool = True) -> str:
    """
    获取当前请求上下文中的 Cookie

    Args:
        fallback_to_env: 当上下文没有 Cookie 时，是否回退到环境变量 HTTP_COOKIE

    Returns:
        Cookie 字符串，如果没有则返回空字符串
    """
    cookie = current_cookie.get()
    if cookie is None and fallback_to_env:
        return os.environ.get("HTTP_COOKIE", "")
    return cookie or ""


def set_current_cookie(cookie: str) -> None:
    """
    设置当前请求上下文中的 Cookie

    Args:
        cookie: Cookie 字符串
    """
    current_cookie.set(cookie)


def submit_with_context(executor, fn, *args, **kwargs):
    """
    提交任务到线程池，并使用 copy_context 传递当前上下文

    使用方式替代 executor.submit(fn, *args, **kwargs)

    Args:
        executor: ThreadPoolExecutor 实例
        fn: 要执行的函数
        *args, **kwargs: 函数参数

    Returns:
        Future 对象
    """
    # 捕获当前上下文
    ctx = contextvars.copy_context()

    def wrapper():
        # 在复制的上下文中运行函数
        return ctx.run(lambda: fn(*args, **kwargs))

    return executor.submit(wrapper)
