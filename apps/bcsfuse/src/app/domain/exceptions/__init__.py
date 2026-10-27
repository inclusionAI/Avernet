"""
Domain Exceptions

领域异常定义，用于表达领域层面的错误。

异常分类：
- DomainException: 基础领域异常
- Worker 相关异常
- 校验相关异常
"""

from __future__ import annotations

from typing import Any, Optional


# =============================================================================
# Legacy Code 显式映射表（覆盖自动生成）
# =============================================================================

LEGACY_CODE_MAPPING: dict[str, str] = {
    "DOMAIN_ERROR": "BCSFUSE-DOM-GENERAL-ERROR",
    "VALIDATION_ERROR": "BCSFUSE-DOM-VALIDATION-ERROR",
}


def _resolve_domain_error_code(code: str, error_code: Optional[str] = None) -> str:
    """
    解析 domain 层标准错误码

    优先级：
    1. 显式指定的 error_code
    2. LEGACY_CODE_MAPPING 中的映射
    3. 自动生成 BCSFUSE-DOM-{CODE}

    Args:
        code: 兼容的错误码字段（如 WORKER_NOT_FOUND）
        error_code: 显式指定的标准错误码（可选）

    Returns:
        标准错误码字符串
    """
    if error_code is not None:
        return error_code
    if code in LEGACY_CODE_MAPPING:
        return LEGACY_CODE_MAPPING[code]
    return f"BCSFUSE-DOM-{code.upper().replace('_', '-')}"


class DomainException(Exception):
    """
    基础领域异常（扩展 error_code 支持）

    所有领域异常的基类。

    Attributes:
        message: 错误消息
        code: 兼容字段，保持不变（如 WORKER_NOT_FOUND）
        error_code: 标准错误码（如 BCSFUSE-DOM-WORKER-NOT-FOUND）
        details: 额外详情
    """

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code or "DOMAIN_ERROR"
        self.details = details or {}

        # 使用解析函数生成 error_code
        self.error_code = _resolve_domain_error_code(self.code, error_code)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        result = {
            "code": self.code,
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result


# =============================================================================
# Worker 相关异常
# =============================================================================

class WorkerException(DomainException):
    """Worker 相关异常基类"""

    def __init__(
        self,
        message: str,
        worker_id: Optional[str] = None,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(message, code, details, error_code)
        self.worker_id = worker_id


class WorkerNotFoundException(WorkerException):
    """Worker 不存在"""

    def __init__(self, worker_id: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=f"Worker not found: {worker_id}",
            worker_id=worker_id,
            code="WORKER_NOT_FOUND",
            details=details,
        )


class DuplicateWorkerException(WorkerException):
    """Worker 已存在（重复创建）"""

    def __init__(self, worker_id: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=f"Worker already exists: {worker_id}",
            worker_id=worker_id,
            code="DUPLICATE_WORKER",
            details=details,
        )


class InvalidWorkerTypeException(WorkerException):
    """无效的 Worker 类型"""

    def __init__(self, worker_type: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=f"Invalid worker type: {worker_type}. Must be 'human' or 'bot'.",
            code="INVALID_WORKER_TYPE",
            details={"provided_type": worker_type, **(details or {})},
        )


class InvalidWorkerIdException(WorkerException):
    """无效的 Worker ID 格式"""

    def __init__(self, worker_id: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=f"Invalid worker ID format: {worker_id}. Must be non-empty string.",
            worker_id=worker_id,
            code="INVALID_WORKER_ID",
            details=details,
        )


# =============================================================================
# 校验相关异常
# =============================================================================

class ValidationException(DomainException):
    """校验异常基类"""

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(message, code or "VALIDATION_ERROR", details, error_code)


class DomainValidationError(ValidationException):
    """领域校验错误"""

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        value: Optional[Any] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        detail_dict = details or {}
        if field:
            detail_dict["field"] = field
        if value is not None:
            detail_dict["value"] = value
        super().__init__(message, "VALIDATION_ERROR", detail_dict)
        self.field = field
        self.value = value


class SchemaValidationError(ValidationException):
    """Schema 校验错误"""

    def __init__(
        self,
        message: str,
        schema_name: Optional[str] = None,
        validation_errors: Optional[list[dict]] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        detail_dict = details or {}
        if schema_name:
            detail_dict["schema"] = schema_name
        if validation_errors:
            detail_dict["validation_errors"] = validation_errors
        super().__init__(message, "SCHEMA_VALIDATION_ERROR", detail_dict)
        self.schema_name = schema_name
        self.validation_errors = validation_errors


# =============================================================================
# FusedProfile 相关异常
# =============================================================================

class FusionException(DomainException):
    """FusedProfile 相关异常基类"""

    def __init__(
        self,
        message: str,
        fusion_id: Optional[str] = None,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(message, code, details, error_code)
        self.fusion_id = fusion_id


class FusionNotFoundException(FusionException):
    """FusedProfile 不存在"""

    def __init__(self, fusion_id: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=f"Fusion not found: {fusion_id}",
            fusion_id=fusion_id,
            code="FUSION_NOT_FOUND",
            details=details,
        )


class DuplicateFusionException(FusionException):
    """FusedProfile 已存在（重复创建）"""

    def __init__(self, fusion_id: str, details: Optional[dict[str, Any]] = None):
        super().__init__(
            message=f"Fusion already exists: {fusion_id}",
            fusion_id=fusion_id,
            code="DUPLICATE_FUSION",
            details=details,
        )


__all__ = [
    # 映射表
    "LEGACY_CODE_MAPPING",
    # 基础异常
    "DomainException",
    # Worker 异常
    "WorkerException",
    "WorkerNotFoundException",
    "DuplicateWorkerException",
    "InvalidWorkerTypeException",
    "InvalidWorkerIdException",
    # 校验异常
    "ValidationException",
    "DomainValidationError",
    "SchemaValidationError",
    # FusedProfile 异常
    "FusionException",
    "FusionNotFoundException",
    "DuplicateFusionException",
]