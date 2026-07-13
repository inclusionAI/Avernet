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
from contextvars import ContextVar

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")

# 保存原始 LogRecord 工厂，用于包装
_original_log_record_factory = logging.getLogRecordFactory()


def _trace_aware_record_factory(*args, **kwargs) -> logging.LogRecord:
    """
    自定义 LogRecord 工厂，在创建每条日志记录时注入 traceid 和 trace_id。

    使用 setLogRecordFactory 替代 Filter，确保在任何 formatter 格式化之前
    字段就已存在，避免 KeyError: 'traceid'。

    两种格式均可使用：
    - %(traceid)s  — 空串时显示 "-"
    - %(trace_id)s — 空串时显示 ""
    """
    record = _original_log_record_factory(*args, **kwargs)
    tid = _trace_id.get()
    record.traceid = tid or "-"  # type: ignore[attr-defined]
    record.trace_id = tid  # type: ignore[attr-defined]
    return record


def install_trace_record_factory() -> None:
    """
    安装自定义 LogRecord 工厂，使所有日志记录自动携带 traceid/trace_id。

    应在应用启动时调用一次（configure_logging 中自动调用）。
    LogRecord 创建时就注入字段，避免 formatter 找不到字段而报 KeyError。
    """
    current_factory = logging.getLogRecordFactory()
    # 避免重复安装
    if current_factory is not _trace_aware_record_factory:
        logging.setLogRecordFactory(_trace_aware_record_factory)


def generate_trace_id() -> str:
    """生成 trace_id，格式: trace_{timestamp_ms}_{8位随机hex}"""
    return f"trace_{int(time.time() * 1000)}_{secrets.token_hex(4)}"


def set_trace_id(trace_id: str) -> None:
    """设置当前请求的 trace_id"""
    _trace_id.set(trace_id)


def get_trace_id() -> str:
    """读取当前请求的 trace_id"""
    return _trace_id.get()


__all__ = [
    "generate_trace_id",
    "set_trace_id",
    "get_trace_id",
    "install_trace_record_factory",
]