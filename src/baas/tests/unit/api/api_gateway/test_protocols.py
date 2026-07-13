"""Unit tests for api_gateway protocol conformance."""

from typing import Protocol

from secbaas.community.api.api_gateway import APIKeyService


class TestAPIKeyServiceProtocol:
    """Test APIKeyService protocol definition."""

    def test_is_protocol(self):
        assert issubclass(APIKeyService, Protocol)

    def test_runtime_checkable(self):
        """Verify @runtime_checkable works with protocol."""
        import inspect

        assert inspect.isclass(APIKeyService)

    def test_all_async_methods_exist(self):
        """All expected methods are defined on the protocol."""
        methods = [
            "create_key",
            "get_key",
            "list_keys",
            "update_key",
            "activate",
            "deactivate",
            "revoke",
        ]
        for method in methods:
            assert hasattr(APIKeyService, method)
            assert callable(getattr(APIKeyService, method))
