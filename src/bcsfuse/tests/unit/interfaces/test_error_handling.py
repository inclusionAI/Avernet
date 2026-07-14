"""
Error Handling Tests

验证接口层统一错误处理模块 - AC-3, AC-4
"""

import pytest
from unittest.mock import patch, MagicMock


class TestLogException:
    """log_exception 函数测试 - AC-4"""

    def test_log_exception_returns_error_code(self):
        """验证 log_exception 返回解析后的 error_code"""
        from src.domain.exceptions import WorkerNotFoundException
        from src.interfaces.api.error_handling import log_exception

        exc = WorkerNotFoundException("bot-123")
        error_code = log_exception(exc)

        assert error_code == "BCSFUSE-DOM-WORKER-NOT-FOUND"

    def test_log_exception_includes_context(self):
        """验证 log_exception 包含上下文信息"""
        from src.domain.exceptions import DomainException
        from src.interfaces.api.error_handling import log_exception

        with patch("src.interfaces.api.error_handling.logger") as mock_logger:
            exc = DomainException("Test error", code="TEST_ERROR")
            log_exception(exc, context={"worker_id": "bot-456", "request_id": "req-789"})

            # 验证 logger.error 被调用
            assert mock_logger.error.called
            # 标准 logging 使用位置参数：logger.error(msg, *args, **kwargs)
            call_args = mock_logger.error.call_args
            # 检查 msg 参数包含关键信息
            msg = call_args[0][0] if call_args[0] else ""
            # 或者检查 extra 参数
            kwargs = call_args[1]

    def test_log_exception_includes_details(self):
        """验证 log_exception 包含异常 details"""
        from src.domain.exceptions import WorkerNotFoundException
        from src.interfaces.api.error_handling import log_exception

        with patch("src.interfaces.api.error_handling.logger") as mock_logger:
            exc = WorkerNotFoundException("bot-123", details={"reason": "not registered"})
            log_exception(exc)

            # 验证 logger.error 被调用
            assert mock_logger.error.called

    def test_log_exception_includes_exception_type(self):
        """验证日志输出包含 exception_type"""
        from src.domain.exceptions import DuplicateWorkerException
        from src.interfaces.api.error_handling import log_exception

        with patch("src.interfaces.api.error_handling.logger") as mock_logger:
            exc = DuplicateWorkerException("bot-123")
            log_exception(exc)

            # 验证 logger.error 被调用
            assert mock_logger.error.called


class TestExceptionToResponse:
    """exception_to_response 函数测试 - AC-3"""

    def test_exception_to_response_returns_json_response(self):
        """验证返回 JSONResponse"""
        from src.domain.exceptions import WorkerNotFoundException
        from src.interfaces.api.error_handling import exception_to_response
        from fastapi.responses import JSONResponse

        exc = WorkerNotFoundException("bot-123")
        response = exception_to_response(exc, status_code=404)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 404

    def test_exception_to_response_includes_error_code(self):
        """验证响应包含 error_code"""
        from src.domain.exceptions import WorkerNotFoundException
        from src.interfaces.api.error_handling import exception_to_response
        import json

        exc = WorkerNotFoundException("bot-123")
        response = exception_to_response(exc, status_code=404)

        # 解析响应体
        body = json.loads(response.body)
        assert body["error_code"] == "BCSFUSE-DOM-WORKER-NOT-FOUND"

    def test_exception_to_response_includes_message(self):
        """验证响应包含 message"""
        from src.domain.exceptions import DuplicateWorkerException
        from src.interfaces.api.error_handling import exception_to_response
        import json

        exc = DuplicateWorkerException("bot-456")
        response = exception_to_response(exc, status_code=409)

        body = json.loads(response.body)
        assert "message" in body
        assert "bot-456" in body["message"]

    def test_exception_to_response_includes_details_for_domain_exception(self):
        """验证 DomainException 响应包含 details"""
        from src.domain.exceptions import InvalidWorkerTypeException
        from src.interfaces.api.error_handling import exception_to_response
        import json

        exc = InvalidWorkerTypeException("invalid-type", details={"hint": "use 'human' or 'bot'"})
        response = exception_to_response(exc, status_code=400)

        body = json.loads(response.body)
        assert "details" in body
        assert body["details"]["hint"] == "use 'human' or 'bot'"
        assert body["details"]["provided_type"] == "invalid-type"

    def test_exception_to_response_no_details_for_non_domain_exception(self):
        """验证非 DomainException 响应不包含 details 字段"""
        from src.interfaces.api.error_handling import exception_to_response
        import json

        exc = ValueError("Invalid input")
        response = exception_to_response(exc, status_code=400)

        body = json.loads(response.body)
        assert "details" not in body
        assert body["error_code"] == "BCSFUSE-IF-INTERNAL-ERROR"  # fallback

    def test_exception_to_response_calls_log_exception(self):
        """验证 exception_to_response 调用 log_exception"""
        from src.domain.exceptions import WorkerNotFoundException
        from src.interfaces.api.error_handling import exception_to_response

        with patch("src.interfaces.api.error_handling.log_exception", return_value="BCSFUSE-DOM-WORKER-NOT-FOUND") as mock_log:
            exc = WorkerNotFoundException("bot-123")
            response = exception_to_response(exc, status_code=404, context={"request_id": "req-123"})

            # 验证 log_exception 被调用
            assert mock_log.called
            # log_exception(exc, context) - context 作为第二个位置参数传递
            assert mock_log.call_args.args[0] == exc
            assert mock_log.call_args.args[1] == {"request_id": "req-123"}


class TestGetStatusCodeForException:
    """get_status_code_for_exception 函数测试"""

    def test_worker_not_found_returns_404(self):
        """验证 WorkerNotFoundException 返回 404"""
        from src.domain.exceptions import WorkerNotFoundException
        from src.interfaces.api.error_handling import get_status_code_for_exception

        exc = WorkerNotFoundException("bot-123")
        assert get_status_code_for_exception(exc) == 404

    def test_file_not_found_returns_404(self):
        """验证 FileNotFoundError 返回 404"""
        from src.interfaces.api.error_handling import get_status_code_for_exception

        exc = FileNotFoundError("config.yaml")
        assert get_status_code_for_exception(exc) == 404

    def test_duplicate_worker_returns_409(self):
        """验证 DuplicateWorkerException 返回 409"""
        from src.domain.exceptions import DuplicateWorkerException
        from src.interfaces.api.error_handling import get_status_code_for_exception

        exc = DuplicateWorkerException("bot-123")
        assert get_status_code_for_exception(exc) == 409

    def test_invalid_worker_id_returns_400(self):
        """验证 InvalidWorkerIdException 返回 400"""
        from src.domain.exceptions import InvalidWorkerIdException
        from src.interfaces.api.error_handling import get_status_code_for_exception

        exc = InvalidWorkerIdException("")
        assert get_status_code_for_exception(exc) == 400

    def test_invalid_worker_type_returns_400(self):
        """验证 InvalidWorkerTypeException 返回 400"""
        from src.domain.exceptions import InvalidWorkerTypeException
        from src.interfaces.api.error_handling import get_status_code_for_exception

        exc = InvalidWorkerTypeException("invalid")
        assert get_status_code_for_exception(exc) == 400

    def test_domain_validation_error_returns_400(self):
        """验证 DomainValidationError 返回 400"""
        from src.domain.exceptions import DomainValidationError
        from src.interfaces.api.error_handling import get_status_code_for_exception

        exc = DomainValidationError("Invalid value", field="name")
        assert get_status_code_for_exception(exc) == 400

    def test_schema_validation_error_returns_400(self):
        """验证 SchemaValidationError 返回 400"""
        from src.domain.exceptions import SchemaValidationError
        from src.interfaces.api.error_handling import get_status_code_for_exception

        exc = SchemaValidationError("Schema validation failed")
        assert get_status_code_for_exception(exc) == 400

    def test_value_error_returns_400(self):
        """验证 ValueError 返回 400"""
        from src.interfaces.api.error_handling import get_status_code_for_exception

        exc = ValueError("Invalid value")
        assert get_status_code_for_exception(exc) == 400

    def test_io_error_returns_503(self):
        """验证 IOError 返回 503"""
        from src.interfaces.api.error_handling import get_status_code_for_exception

        exc = IOError("Disk full")
        assert get_status_code_for_exception(exc) == 503

    def test_unknown_error_returns_500(self):
        """验证未知异常返回 500"""
        from src.interfaces.api.error_handling import get_status_code_for_exception

        exc = RuntimeError("Unexpected error")
        assert get_status_code_for_exception(exc) == 500