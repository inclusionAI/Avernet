"""Shared conformance suite for the optional default-model provider contract.

Implementations supply default_model_factory(mode) with configured, unset and
failed upstream fixtures. The suite is intentionally shared across plugins.
"""
import pytest

from engine.community.core.models.default_model import DefaultModelService


class DefaultModelContract:
    @pytest.mark.parametrize("mode,expected", [("configured", "provider/model"), ("unset", None)])
    async def test_default_result(self, default_model_factory, mode, expected):
        service = default_model_factory(mode)
        assert isinstance(service, DefaultModelService)
        assert await service.get_default_model() == expected

    async def test_failure_propagates(self, default_model_factory):
        service = default_model_factory("failed")
        with pytest.raises((ValueError, RuntimeError, ConnectionError)):
            await service.get_default_model()
