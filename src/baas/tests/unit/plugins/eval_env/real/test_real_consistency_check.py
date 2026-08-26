"""RealEvalConsistencyCheck 单元测试。"""

from unittest.mock import MagicMock

from secbaas.community.api.eval_env import DYNAMIC_ENV_TAG_KEY
from secbaas.community.plugins.eval_env.real._real_consistency_check import (
    RealEvalConsistencyCheck,
)


def _make_binding_info(device_props=None):
    """构造 mock binding_info，支持 device_props。"""
    info = MagicMock()
    if device_props is not None:
        info.device_props = device_props
    else:
        # 模拟真实对象有 device_props 属性
        del info.device_props
    return info


class TestRealEvalConsistencyCheck:
    def test_consistent_tags_returns_true(self):
        checker = RealEvalConsistencyCheck()
        binding_info = _make_binding_info(device_props={DYNAMIC_ENV_TAG_KEY: "staging"})
        result = checker.check_default_tag_consistency(
            binding_info=binding_info,
            chat_metadata={"default_tag": "staging"},
        )
        assert result is True

    def test_inconsistent_tags_returns_false(self):
        checker = RealEvalConsistencyCheck()
        binding_info = _make_binding_info(
            device_props={DYNAMIC_ENV_TAG_KEY: "production"}
        )
        result = checker.check_default_tag_consistency(
            binding_info=binding_info,
            chat_metadata={"default_tag": "staging"},
        )
        assert result is False

    def test_no_metadata_tag_returns_true(self):
        checker = RealEvalConsistencyCheck()
        binding_info = _make_binding_info(device_props={DYNAMIC_ENV_TAG_KEY: "staging"})
        result = checker.check_default_tag_consistency(
            binding_info=binding_info,
            chat_metadata={},
        )
        assert result is True

    def test_metadata_tag_no_binding_tag_returns_true(self):
        checker = RealEvalConsistencyCheck()
        binding_info = _make_binding_info(device_props={})
        result = checker.check_default_tag_consistency(
            binding_info=binding_info,
            chat_metadata={"default_tag": "staging"},
        )
        assert result is True

    def test_none_binding_info_returns_true(self):
        checker = RealEvalConsistencyCheck()
        result = checker.check_default_tag_consistency(
            binding_info=None,
            chat_metadata={"default_tag": "staging"},
        )
        assert result is True

    def test_none_binding_info_no_metadata_tag_returns_true(self):
        checker = RealEvalConsistencyCheck()
        result = checker.check_default_tag_consistency(
            binding_info=None,
            chat_metadata={},
        )
        assert result is True

    def test_binding_info_without_device_props_no_metadata_tag(self):
        checker = RealEvalConsistencyCheck()
        binding_info = _make_binding_info(device_props=None)
        result = checker.check_default_tag_consistency(
            binding_info=binding_info,
            chat_metadata={},
        )
        assert result is True
