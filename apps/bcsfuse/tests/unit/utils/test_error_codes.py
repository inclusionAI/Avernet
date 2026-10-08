"""
Error Codes Tests

验证统一错误码处理模块 - AC-2
"""

import pytest


class TestResolveErrorCode:
    """resolve_error_code 函数测试"""

    def test_resolve_error_code_from_domain_exception(self):
        """验证从 DomainException 提取 error_code"""
        from src.domain.exceptions import WorkerNotFoundException
        from src.utils.error_codes import resolve_error_code

        exc = WorkerNotFoundException("bot-123")
        assert resolve_error_code(exc) == "BCSFUSE-DOM-WORKER-NOT-FOUND"

    def test_resolve_error_code_from_cause_chain(self):
        """验证从 cause 链中提取 error_code"""
        from src.domain.exceptions import WorkerNotFoundException
        from src.utils.error_codes import resolve_error_code

        # 创建内层异常（携带 error_code）
        inner = WorkerNotFoundException("bot-123")
        # 创建外层异常（不携带 error_code）
        outer = ValueError("Wrapper error")
        outer.__cause__ = inner

        # 从 cause 链中找到 inner 的 error_code
        assert resolve_error_code(outer) == "BCSFUSE-DOM-WORKER-NOT-FOUND"

    def test_resolve_error_code_from_cause_chain_nested(self):
        """验证多层嵌套的 cause 链提取"""
        from src.domain.exceptions import DuplicateWorkerException
        from src.utils.error_codes import resolve_error_code

        # 最内层异常
        inner = DuplicateWorkerException("bot-456")
        # 中间层异常
        middle = RuntimeError("Middle error")
        middle.__cause__ = inner
        # 最外层异常
        outer = ValueError("Outer error")
        outer.__cause__ = middle

        # 应该从最内层提取 error_code
        assert resolve_error_code(outer) == "BCSFUSE-DOM-DUPLICATE-WORKER"

    def test_resolve_error_code_from_context_chain(self):
        """验证从 __context__ 链中提取 error_code"""
        from src.domain.exceptions import WorkerNotFoundException
        from src.utils.error_codes import resolve_error_code

        inner = WorkerNotFoundException("bot-789")
        outer = ValueError("Wrapper error")
        outer.__context__ = inner  # 使用 __context__ 而非 __cause__

        assert resolve_error_code(outer) == "BCSFUSE-DOM-WORKER-NOT-FOUND"

    def test_resolve_error_code_from_mapping(self):
        """验证 INFRA_EXCEPTION_MAPPING 映射"""
        from src.utils.error_codes import resolve_error_code

        # IOError 应该映射到 BCSFUSE-INFRA-STORAGE-ERROR
        exc = IOError("Disk full")
        assert resolve_error_code(exc, layer="infrastructure") == "BCSFUSE-INFRA-STORAGE-ERROR"

    def test_resolve_error_code_file_not_found_mapping(self):
        """验证 FileNotFoundError 映射"""
        from src.utils.error_codes import resolve_error_code

        exc = FileNotFoundError("File not found")
        assert resolve_error_code(exc) == "BCSFUSE-INFRA-STORAGE-NOT-FOUND"

    def test_resolve_error_code_layer_fallback_interfaces(self):
        """验证 interfaces 层级 fallback"""
        from src.utils.error_codes import resolve_error_code

        exc = RuntimeError("Unknown error")
        assert resolve_error_code(exc, layer="interfaces") == "BCSFUSE-IF-INTERNAL-ERROR"

    def test_resolve_error_code_layer_fallback_application(self):
        """验证 application 层级 fallback"""
        from src.utils.error_codes import resolve_error_code

        exc = RuntimeError("Unknown error")
        assert resolve_error_code(exc, layer="application") == "BCSFUSE-APP-INTERNAL-ERROR"

    def test_resolve_error_code_layer_fallback_domain(self):
        """验证 domain 层级 fallback"""
        from src.utils.error_codes import resolve_error_code

        exc = RuntimeError("Unknown error")
        assert resolve_error_code(exc, layer="domain") == "BCSFUSE-DOM-INTERNAL-ERROR"

    def test_resolve_error_code_layer_fallback_infrastructure(self):
        """验证 infrastructure 层级 fallback"""
        from src.utils.error_codes import resolve_error_code

        exc = RuntimeError("Unknown error")
        assert resolve_error_code(exc, layer="infrastructure") == "BCSFUSE-INFRA-INTERNAL-ERROR"

    def test_resolve_error_code_default_fallback(self):
        """验证无层级参数时的默认 fallback"""
        from src.utils.error_codes import resolve_error_code

        exc = RuntimeError("Unknown error")
        assert resolve_error_code(exc) == "BCSFUSE-INTERNAL-ERROR"

    def test_resolve_error_code_prioritizes_explicit_error_code(self):
        """验证显式指定的 error_code 优先级最高"""
        from src.domain.exceptions import DomainException
        from src.utils.error_codes import resolve_error_code

        exc = DomainException(
            message="Custom error",
            code="SOME_CODE",
            error_code="BCSFUSE-DOM-CUSTOM-CODE"
        )
        assert resolve_error_code(exc) == "BCSFUSE-DOM-CUSTOM-CODE"

    def test_resolve_error_code_does_not_map_value_error(self):
        """验证不映射 ValueError 等通用异常"""
        from src.utils.error_codes import resolve_error_code, INFRA_EXCEPTION_MAPPING

        # ValueError 不应该在映射表中
        assert ValueError not in INFRA_EXCEPTION_MAPPING

        # ValueError 应该使用 fallback
        exc = ValueError("Invalid value")
        assert resolve_error_code(exc, layer="domain") == "BCSFUSE-DOM-INTERNAL-ERROR"


class TestResolveErrorCodeFromLayer:
    """resolve_error_code_from_layer 函数测试"""

    def test_interfaces_layer_fallback(self):
        """验证 interfaces 层 fallback"""
        from src.utils.error_codes import resolve_error_code_from_layer

        assert resolve_error_code_from_layer("interfaces") == "BCSFUSE-IF-INTERNAL-ERROR"

    def test_application_layer_fallback(self):
        """验证 application 层 fallback"""
        from src.utils.error_codes import resolve_error_code_from_layer

        assert resolve_error_code_from_layer("application") == "BCSFUSE-APP-INTERNAL-ERROR"

    def test_domain_layer_fallback(self):
        """验证 domain 层 fallback"""
        from src.utils.error_codes import resolve_error_code_from_layer

        assert resolve_error_code_from_layer("domain") == "BCSFUSE-DOM-INTERNAL-ERROR"

    def test_infrastructure_layer_fallback(self):
        """验证 infrastructure 层 fallback"""
        from src.utils.error_codes import resolve_error_code_from_layer

        assert resolve_error_code_from_layer("infrastructure") == "BCSFUSE-INFRA-INTERNAL-ERROR"

    def test_unknown_layer_fallback(self):
        """验证未知层级使用默认 fallback"""
        from src.utils.error_codes import resolve_error_code_from_layer

        assert resolve_error_code_from_layer("unknown") == "BCSFUSE-INTERNAL-ERROR"
        assert resolve_error_code_from_layer("") == "BCSFUSE-INTERNAL-ERROR"


class TestInfraExceptionMapping:
    """INFRA_EXCEPTION_MAPPING 测试"""

    def test_mapping_exists(self):
        """验证映射表存在"""
        from src.utils.error_codes import INFRA_EXCEPTION_MAPPING

        assert INFRA_EXCEPTION_MAPPING is not None
        assert isinstance(INFRA_EXCEPTION_MAPPING, dict)

    def test_io_error_mapping(self):
        """验证 IOError 映射"""
        from src.utils.error_codes import INFRA_EXCEPTION_MAPPING

        assert IOError in INFRA_EXCEPTION_MAPPING
        assert INFRA_EXCEPTION_MAPPING[IOError] == "BCSFUSE-INFRA-STORAGE-ERROR"

    def test_file_not_found_mapping(self):
        """验证 FileNotFoundError 映射"""
        from src.utils.error_codes import INFRA_EXCEPTION_MAPPING

        assert FileNotFoundError in INFRA_EXCEPTION_MAPPING
        assert INFRA_EXCEPTION_MAPPING[FileNotFoundError] == "BCSFUSE-INFRA-STORAGE-NOT-FOUND"


class TestLayerFallback:
    """LAYER_FALLBACK 测试"""

    def test_layer_fallback_exists(self):
        """验证层级 fallback 定义存在"""
        from src.utils.error_codes import LAYER_FALLBACK

        assert LAYER_FALLBACK is not None
        assert isinstance(LAYER_FALLBACK, dict)
        assert len(LAYER_FALLBACK) == 4

    def test_default_fallback(self):
        """验证默认 fallback"""
        from src.utils.error_codes import DEFAULT_FALLBACK

        assert DEFAULT_FALLBACK == "BCSFUSE-INTERNAL-ERROR"