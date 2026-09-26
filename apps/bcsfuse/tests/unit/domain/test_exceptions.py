"""
Domain Exceptions Tests

验证领域异常结构。
"""

import pytest


class TestDomainExceptions:
    """领域异常测试"""

    def test_domain_exception_importable(self):
        """验证 DomainException 可导入"""
        from src.domain.exceptions import DomainException
        assert DomainException is not None

    def test_domain_exception_is_exception(self):
        """验证 DomainException 是 Exception 的子类"""
        from src.domain.exceptions import DomainException
        assert issubclass(DomainException, Exception)

    def test_domain_exception_with_message(self):
        """验证 DomainException 可以携带消息"""
        from src.domain.exceptions import DomainException

        exc = DomainException("Test error message")
        assert str(exc) == "Test error message"

    def test_domain_exception_with_code(self):
        """验证 DomainException 可以携带错误码"""
        from src.domain.exceptions import DomainException

        exc = DomainException("Test error", code="TEST_ERROR")
        assert exc.code == "TEST_ERROR"
        assert str(exc) == "Test error"

    def test_worker_not_found_exception(self):
        """验证 WorkerNotFoundException 存在"""
        from src.domain.exceptions import WorkerNotFoundException

        exc = WorkerNotFoundException("wrk_test")
        assert "wrk_test" in str(exc)
        assert exc.code == "WORKER_NOT_FOUND"

    def test_duplicate_worker_exception(self):
        """验证 DuplicateWorkerException 存在"""
        from src.domain.exceptions import DuplicateWorkerException

        exc = DuplicateWorkerException("wrk_test")
        assert "wrk_test" in str(exc)
        assert exc.code == "DUPLICATE_WORKER"

    def test_validation_error_exception(self):
        """验证 DomainValidationError 存在"""
        from src.domain.exceptions import DomainValidationError

        exc = DomainValidationError("Invalid field", field="name", value=None)
        assert "Invalid field" in str(exc)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.field == "name"

    def test_invalid_worker_type_exception(self):
        """验证 InvalidWorkerTypeException 存在"""
        from src.domain.exceptions import InvalidWorkerTypeException

        exc = InvalidWorkerTypeException("invalid_type")
        assert "invalid_type" in str(exc)
        assert exc.code == "INVALID_WORKER_TYPE"


class TestErrorCodes:
    """错误码测试 - AC-1, AC-8"""

    def test_domain_exception_auto_generates_error_code(self):
        """验证 DomainException 自动生成 error_code"""
        from src.domain.exceptions import DomainException

        exc = DomainException(message="Test error", code="TEST_ERROR")
        assert exc.code == "TEST_ERROR"
        assert exc.error_code == "BCSFUSE-DOM-TEST-ERROR"

    def test_domain_exception_uses_resolve_function_for_legacy_codes(self):
        """验证 LEGACY_CODE_MAPPING 生效 - DOMAIN_ERROR 映射到 BCSFUSE-DOM-GENERAL-ERROR"""
        from src.domain.exceptions import DomainException

        exc = DomainException(message="Domain error", code="DOMAIN_ERROR")
        # LEGACY_CODE_MAPPING 生效
        assert exc.error_code == "BCSFUSE-DOM-GENERAL-ERROR"
        # code 字段保持不变
        assert exc.code == "DOMAIN_ERROR"

    def test_domain_exception_uses_resolve_function_for_validation_error(self):
        """验证 LEGACY_CODE_MAPPING 生效 - VALIDATION_ERROR 映射到 BCSFUSE-DOM-VALIDATION-ERROR"""
        from src.domain.exceptions import DomainException

        exc = DomainException(message="Validation error", code="VALIDATION_ERROR")
        assert exc.error_code == "BCSFUSE-DOM-VALIDATION-ERROR"
        assert exc.code == "VALIDATION_ERROR"

    def test_domain_exception_preserves_explicit_error_code(self):
        """验证显式指定 error_code 时不会被覆盖"""
        from src.domain.exceptions import DomainException

        exc = DomainException(
            message="Test error",
            code="TEST_ERROR",
            error_code="BCSFUSE-DOM-CUSTOM-CODE"
        )
        assert exc.error_code == "BCSFUSE-DOM-CUSTOM-CODE"
        assert exc.code == "TEST_ERROR"

    def test_worker_not_found_exception_error_code(self):
        """验证 WorkerNotFoundException 的 error_code"""
        from src.domain.exceptions import WorkerNotFoundException

        exc = WorkerNotFoundException("bot-123")
        assert exc.code == "WORKER_NOT_FOUND"
        assert exc.error_code == "BCSFUSE-DOM-WORKER-NOT-FOUND"
        assert "bot-123" in str(exc)

    def test_duplicate_worker_exception_error_code(self):
        """验证 DuplicateWorkerException 的 error_code"""
        from src.domain.exceptions import DuplicateWorkerException

        exc = DuplicateWorkerException("bot-456")
        assert exc.code == "DUPLICATE_WORKER"
        assert exc.error_code == "BCSFUSE-DOM-DUPLICATE-WORKER"

    def test_invalid_worker_id_exception_error_code(self):
        """验证 InvalidWorkerIdException 的 error_code"""
        from src.domain.exceptions import InvalidWorkerIdException

        exc = InvalidWorkerIdException("")
        assert exc.code == "INVALID_WORKER_ID"
        assert exc.error_code == "BCSFUSE-DOM-INVALID-WORKER-ID"

    def test_invalid_worker_type_exception_error_code(self):
        """验证 InvalidWorkerTypeException 的 error_code"""
        from src.domain.exceptions import InvalidWorkerTypeException

        exc = InvalidWorkerTypeException("invalid")
        assert exc.code == "INVALID_WORKER_TYPE"
        assert exc.error_code == "BCSFUSE-DOM-INVALID-WORKER-TYPE"

    def test_to_dict_includes_error_code(self):
        """验证 to_dict() 方法包含 error_code"""
        from src.domain.exceptions import WorkerNotFoundException

        exc = WorkerNotFoundException("bot-789", details={"reason": "not registered"})
        result = exc.to_dict()

        assert "error_code" in result
        assert result["error_code"] == "BCSFUSE-DOM-WORKER-NOT-FOUND"
        assert result["code"] == "WORKER_NOT_FOUND"
        assert result["message"] == "Worker not found: bot-789"
        assert result["details"]["reason"] == "not registered"

    def test_legacy_code_mapping_exported(self):
        """验证 LEGACY_CODE_MAPPING 被导出"""
        from src.domain.exceptions import LEGACY_CODE_MAPPING

        assert LEGACY_CODE_MAPPING is not None
        assert "DOMAIN_ERROR" in LEGACY_CODE_MAPPING
        assert "VALIDATION_ERROR" in LEGACY_CODE_MAPPING
        assert LEGACY_CODE_MAPPING["DOMAIN_ERROR"] == "BCSFUSE-DOM-GENERAL-ERROR"
        assert LEGACY_CODE_MAPPING["VALIDATION_ERROR"] == "BCSFUSE-DOM-VALIDATION-ERROR"