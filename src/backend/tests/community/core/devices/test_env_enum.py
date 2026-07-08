"""Env enum 行为测试（含 singlebox alias）。"""
import pytest

from agentclaw.community.core.devices.models import Env


class TestEnvFromStringSinglebox:
    """Env.from_string 把 singlebox 映射到 DEV（singlebox 等同 dev 的 DI 行为）。"""

    def test_singlebox_maps_to_dev(self):
        assert Env.from_string("singlebox") == Env.DEV

    def test_singlebox_uppercase_maps_to_dev(self):
        assert Env.from_string("SINGLEBOX") == Env.DEV

    def test_dev_still_maps_to_dev(self):
        assert Env.from_string("dev") == Env.DEV

    def test_invalid_still_raises(self):
        with pytest.raises(ValueError):
            Env.from_string("nonsense")
