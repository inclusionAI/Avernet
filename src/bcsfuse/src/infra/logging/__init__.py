"""
Logging Infrastructure

结构化日志基础设施。

M0: 基础日志配置，后续 Milestone 可以扩展。
"""

from __future__ import annotations

import logging
import sys
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# 导出统一日志配置
from .logging_config import configure_logging


# 默认日志格式 — 使用 %(traceid)s 与 sofapy_base 日志格式一致
DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - [%(process)d] - [%(processName)s] - [%(traceid)s] - %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 日志文件路径（与 local_setup.sh 一致）
DEFAULT_LOG_DIR = Path(__file__).parent.parent.parent.parent.parent.parent / "scripts" / ".dependencies" / "logs"
LOG_FILE = DEFAULT_LOG_DIR / "bcsfuse_app.log"

# 确保日志目录存在
try:
    DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass  # 如果无法创建目录，只使用控制台日志


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的 Logger

    Args:
        name: Logger 名称（通常使用 __name__）

    Returns:
        配置好的 Logger 实例
    """
    logger = logging.getLogger(name)

    # 如果 logger 已经有 handler，不再重复添加
    if logger.handlers:
        return logger

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATE_FORMAT))
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    # 文件 handler（如果目录可写）
    try:
        if DEFAULT_LOG_DIR.exists() and os.access(DEFAULT_LOG_DIR, os.W_OK):
            file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
            file_handler.setFormatter(logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATE_FORMAT))
            file_handler.setLevel(logging.DEBUG)  # 文件记录 DEBUG 级别
            logger.addHandler(file_handler)
    except Exception:
        pass  # 文件日志失败不影响控制台日志

    logger.setLevel(logging.DEBUG)  # 允许 DEBUG 级别通过

    return logger


class StructuredLogAdapter(logging.LoggerAdapter):
    """
    结构化日志适配器

    支持额外的上下文字段。
    """

    def process(
        self,
        msg: str,
        kwargs: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """处理日志消息，添加额外上下文"""
        extra = kwargs.get("extra", {})
        extra.update(self.extra or {})
        kwargs["extra"] = extra
        return msg, kwargs


def create_context_logger(
    base_logger: logging.Logger,
    **context: Any,
) -> StructuredLogAdapter:
    """
    创建带上下文的 Logger

    Args:
        base_logger: 基础 Logger
        **context: 上下文字段（如 request_id, task_id, worker_id 等）

    Returns:
        StructuredLogAdapter 实例
    """
    return StructuredLogAdapter(base_logger, context)


class LogContext:
    """
    日志上下文管理器

    用于记录函数进入和退出。
    """

    def __init__(
        self,
        logger: logging.Logger,
        operation: str,
        **context: Any,
    ):
        self.logger = logger
        self.operation = operation
        self.context = context
        self.start_time: Optional[datetime] = None

    def __enter__(self) -> "LogContext":
        self.start_time = datetime.now(timezone.utc)
        self.logger.info(
            f"Starting {self.operation}",
            extra={"context": self.context},
        )
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        end_time = datetime.now(timezone.utc)
        duration_ms = (end_time - self.start_time).total_seconds() * 1000 if self.start_time else 0

        if exc_type:
            self.logger.error(
                f"Failed {self.operation}: {exc_val}",
                extra={
                    "context": self.context,
                    "duration_ms": duration_ms,
                    "error_type": exc_type.__name__,
                },
            )
        else:
            self.logger.info(
                f"Completed {self.operation}",
                extra={
                    "context": self.context,
                    "duration_ms": duration_ms,
                },
            )


__all__ = [
    "get_logger",
    "configure_logging",
    "StructuredLogAdapter",
    "create_context_logger",
    "LogContext",
]