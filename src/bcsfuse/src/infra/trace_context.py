"""
trace_context — 请求级 trace_id 的生成、存储、读取

使用 contextvars 实现异步安全的请求隔离。
每个请求在独立的 asyncio context 中执行，trace_id 互不干扰。

ID 格式: trace_{timestamp_ms}_{8位随机hex}
示例: trace_1713945605000_a1b2c3d4
"""

import logging
import time
import secrets
from contextlib import contextmanager
from contextvars import ContextVar

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")

def install_trace_record_factory() -> None:
    """
    安装自定义 LogRecord 工厂，使所有日志记录自动携带 traceid/trace_id。

    应在应用启动时调用一次（configure_logging 中自动调用）。
    LogRecord 创建时就注入字段，避免 formatter 找不到字段而报 KeyError。
    """
    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_bcsfuse_trace_factory", False):
        return

    def trace_aware_factory(*args, **kwargs) -> logging.LogRecord:
        # Chain the current factory, including fields installed by the host SDK.
        record = current_factory(*args, **kwargs)
        tid = _trace_id.get()
        record.traceid = tid or getattr(record, "traceid", "-")
        record.trace_id = tid or getattr(record, "trace_id", "")
        return record

    trace_aware_factory._bcsfuse_trace_factory = True
    logging.setLogRecordFactory(trace_aware_factory)


def generate_trace_id() -> str:
    """生成 trace_id，格式: trace_{timestamp_ms}_{8位随机hex}"""
    return f"trace_{int(time.time() * 1000)}_{secrets.token_hex(4)}"


def set_trace_id(trace_id: str) -> None:
    """设置当前请求的 trace_id"""
    _trace_id.set(trace_id)


def get_trace_id() -> str:
    """读取当前请求的 trace_id"""
    return _trace_id.get()


@contextmanager
def bind_trace_id(trace_id: str):
    """Bind a request trace and restore its caller's context on every exit."""
    previous_context = _trace_id.set(trace_id)
    try:
        yield
    finally:
        _trace_id.reset(previous_context)


__all__ = [
    "generate_trace_id",
    "set_trace_id",
    "get_trace_id",
    "install_trace_record_factory",
]
