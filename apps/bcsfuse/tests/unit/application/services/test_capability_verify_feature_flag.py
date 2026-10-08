"""Feature Flag 能力验证开关测试。"""

from __future__ import annotations

import pytest

from src.infra.config.feature_flags import FeatureFlags


class TestCapabilityVerifyFeatureFlag:
    def test_default_is_disabled(self) -> None:
        FeatureFlags.reset()
        assert FeatureFlags.is_capability_verify_enabled() is False

    def test_is_enabled_method(self) -> None:
        FeatureFlags.reset()
        # is_enabled maps "ENABLE_CAPABILITY_VERIFY" → capability_verify
        result = FeatureFlags.is_enabled("ENABLE_CAPABILITY_VERIFY")
        # Default is False
        assert result is False

    def test_get_all_flags_includes_capability_verify(self) -> None:
        FeatureFlags.reset()
        flags = FeatureFlags.get_all_flags()
        assert "ENABLE_CAPABILITY_VERIFY" in flags