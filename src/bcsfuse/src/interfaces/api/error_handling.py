"""
接口层统一错误处理模块

职责：
1. 统一日志输出格式
2. 统一 HTTP 错误响应格式
3. 异常到 error_code 的解析逻辑
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi.responses import JSONResponse

from src.domain.exceptions import DomainException
from src.utils.error_codes import resolve_error_code


logger = logging.getLogger(__name__)


def log_exception(exc: Exception, context: Optional[dict[str, Any]] = None) -> str:
    """
    统一异常日志输出

    Args:
        exc: 异常实例
        context: 额外上下文信息（如 worker_id, request_id 等）

    Returns:
        解析后的 error_code
    """
    # 解析 error_code（优先从异常链中提取）
    error_code = resolve_error_code(exc, layer="interfaces")

    # 构建日志消息
    exception_type = type(exc).__name__
    log_msg = f"Exception occurred: error_code={error_code} exception_type={exception_type} message={exc}"

    # 构建额外信息
    extra: dict[str, Any] = {
        "error_code": error_code,
        "exception_type": exception_type,
    }
    if context:
        extra.update(context)
    if hasattr(exc, 'details') and exc.details:
        extra["details"] = exc.details

    logger.error(log_msg, extra=extra)
    return error_code


def exception_to_response(
    exc: Exception,
    status_code: int = 500,
    context: Optional[dict[str, Any]] = None,
) -> JSONResponse:
    """
    将异常转换为统一的 HTTP 错误响应

    响应格式：
    {
        "error_code": "BCSFUSE-DOM-WORKER-NOT-FOUND",
        "message": "Worker not found: bot-123",
        "details": {...}  // 可选
    }

    Args:
        exc: 异常实例
        status_code: HTTP 状态码
        context: 额外上下文信息（会写入日志，但不写入响应）

    Returns:
        JSONResponse
    """
    # 先记录日志
    error_code = log_exception(exc, context)

    # 构建响应体
    response_body: dict[str, Any] = {
        "error_code": error_code,
        "message": str(exc),
    }

    # details 仅在 DomainException 时添加
    if isinstance(exc, DomainException) and exc.details:
        response_body["details"] = exc.details

    return JSONResponse(status_code=status_code, content=response_body)


# =============================================================================
# HTTP 状态码映射辅助
# =============================================================================

def get_status_code_for_exception(exc: Exception) -> int:
    """
    根据异常类型推断 HTTP 状态码

    Args:
        exc: 异常实例

    Returns:
        HTTP 状态码
    """
    from src.domain.exceptions import (
        WorkerNotFoundException,
        DuplicateWorkerException,
        InvalidWorkerIdException,
        InvalidWorkerTypeException,
        DomainValidationError,
        SchemaValidationError,
    )

    if isinstance(exc, (WorkerNotFoundException, FileNotFoundError)):
        return 404
    if isinstance(exc, DuplicateWorkerException):
        return 409
    if isinstance(exc, (InvalidWorkerIdException, InvalidWorkerTypeException,
                        DomainValidationError, SchemaValidationError)):
        return 400
    if isinstance(exc, ValueError):
        return 400
    if isinstance(exc, IOError):
        return 503
    return 500


__all__ = [
    "log_exception",
    "exception_to_response",
    "get_status_code_for_exception",
]