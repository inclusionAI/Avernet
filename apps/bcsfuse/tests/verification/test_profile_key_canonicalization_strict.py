"""
Profile Key Canonicalization 严格验证测试

这些测试用例专门验证 profile_key 在不同来源之间的格式转换问题。

关键场景：
1. API 注册的 binding 格式: {worker_id}:{profile_id}
2. FILE 导入的 binding 格式: staff_{staff_id}:{profile_id}
3. Profile Source 扫描格式: staff_{staff_id}:{profile_id}
4. 用户请求格式: 可能是任意格式
"""

import pytest
from unittest.mock import MagicMock, patch
from src.domain.services.profile_key_canonicalizer import ProfileKeyCanonicalizer


class TestProfileKeyFormats:
    """测试不同的 profile_key 格式转换"""

    def test_api_binding_to_profile_source_format(self):
        """
        场景：API 注册的 binding 需要匹配 Profile Source 扫描的结果

        API binding 格式: wrk_test_expert:default
        Profile Source 格式: staff_wrk_test_expert:default
        """
        canonicalizer = ProfileKeyCanonicalizer()

        # Profile Source 扫描出的实际格式
        available_keys = {
            "staff_wrk_test_expert:default",
            "staff_wrk_another_expert:default",
        }

        # 用户请求中的格式（与 API binding 格式一致）
        raw_key = "wrk_test_expert:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)

        # 必须匹配到正确的格式
        assert result == "staff_wrk_test_expert:default", \
            f"Expected 'staff_wrk_test_expert:default' but got '{result}'"

    def test_file_binding_to_profile_source_format(self):
        """
        场景：FILE 导入的 binding 格式与 Profile Source 格式一致

        FILE binding 格式: staff_wrk_test_expert:default
        Profile Source 格式: staff_wrk_test_expert:default
        """
        canonicalizer = ProfileKeyCanonicalizer()

        available_keys = {
            "staff_wrk_test_expert:default",
        }

        # FILE binding 格式与 available 格式一致
        raw_key = "staff_wrk_test_expert:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)

        assert result == "staff_wrk_test_expert:default"

    def test_user_request_without_prefix(self):
        """
        场景：用户请求没有前缀

        用户请求: test_expert:default
        Profile Source: staff_wrk_test_expert:default
        """
        canonicalizer = ProfileKeyCanonicalizer()

        available_keys = {
            "staff_wrk_test_expert:default",
            "wrk_test_expert:default",
        }

        raw_key = "test_expert:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)

        # 应该能匹配到其中一种格式
        assert result in available_keys, \
            f"Expected match in available_keys but got '{result}'"


class TestBindingStoreIntegration:
    """测试 Binding Store 集成"""

    def test_binding_store_returns_api_format(self):
        """
        场景：Binding Store 返回的是 API 注册格式

        请求: wrk_test:default
        Binding 返回: worker_id=wrk_test, profile_key=wrk_test:default
        Profile Source: staff_wrk_test:default

        关键：需要从 worker_id 构建 staff_ 格式
        """
        # 模拟 binding
        mock_binding = MagicMock()
        mock_binding.worker_id = "wrk_test_reg_g5_expert"
        mock_binding.profile_key = "wrk_test_reg_g5_expert:default"

        mock_binding_store = MagicMock()
        mock_binding_store.get_binding_by_profile_key.return_value = mock_binding

        canonicalizer = ProfileKeyCanonicalizer(binding_store=mock_binding_store)

        # Profile Source 的实际格式
        available_keys = {
            "staff_wrk_test_reg_g5_expert:default",
        }

        # 用户请求格式（与 binding 格式一致）
        raw_key = "wrk_test_reg_g5_expert:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)

        # 必须通过 worker_id 构建正确的格式
        assert result == "staff_wrk_test_reg_g5_expert:default", \
            f"Expected 'staff_wrk_test_reg_g5_expert:default' but got '{result}'"

        # 验证 binding store 被调用
        mock_binding_store.get_binding_by_profile_key.assert_called_once_with(raw_key)

    def test_binding_store_returns_file_format(self):
        """
        场景：Binding Store 返回的是 FILE 导入格式

        请求: staff_wrk_test:default
        Binding 返回: worker_id=wrk_test, profile_key=staff_wrk_test:default
        Profile Source: staff_wrk_test:default
        """
        mock_binding = MagicMock()
        mock_binding.worker_id = "wrk_test"
        mock_binding.profile_key = "staff_wrk_test:default"

        mock_binding_store = MagicMock()
        mock_binding_store.get_binding_by_profile_key.return_value = mock_binding

        canonicalizer = ProfileKeyCanonicalizer(binding_store=mock_binding_store)

        available_keys = {
            "staff_wrk_test:default",
        }

        raw_key = "staff_wrk_test:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)

        assert result == "staff_wrk_test:default"

    def test_binding_store_not_found_fallback_to_prefix(self):
        """
        场景：Binding Store 找不到，回退到前缀匹配
        """
        mock_binding_store = MagicMock()
        mock_binding_store.get_binding_by_profile_key.return_value = None

        canonicalizer = ProfileKeyCanonicalizer(binding_store=mock_binding_store)

        available_keys = {
            "staff_wrk_test:default",
        }

        raw_key = "wrk_test:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)

        # 应该通过前缀添加匹配
        assert result == "staff_wrk_test:default"


class TestRealWorldScenarios:
    """真实场景测试"""

    def test_prepub_scenario_g5_security_audit(self):
        """
        预发环境真实场景：

        用户请求: wrk_test_reg_g5_security_audit_expert:default
        Profile Source: staff_wrk_test_reg_g5_security_audit_expert:default
        """
        canonicalizer = ProfileKeyCanonicalizer()

        available_keys = {
            "staff_wrk_test_reg_g1_architect:default",
            "staff_wrk_test_reg_g2_ops_expert:default",
            "staff_wrk_test_reg_g5_security_audit_expert:default",
        }

        raw_key = "wrk_test_reg_g5_security_audit_expert:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)

        assert result == "staff_wrk_test_reg_g5_security_audit_expert:default"

    def test_multiple_workers_same_profile(self):
        """
        场景：多个 worker 有相同的 profile_id
        """
        canonicalizer = ProfileKeyCanonicalizer()

        available_keys = {
            "staff_wrk_expert_a:default",
            "staff_wrk_expert_b:default",
            "staff_wrk_expert_c:default",
        }

        # 请求特定 worker
        raw_key = "wrk_expert_b:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)

        assert result == "staff_wrk_expert_b:default"

    def test_profile_id_variations(self):
        """
        场景：不同的 profile_id
        """
        canonicalizer = ProfileKeyCanonicalizer()

        available_keys = {
            "staff_wrk_expert:default",
            "staff_wrk_expert:v1",
            "staff_wrk_expert:v2",
        }

        # 请求 v1 版本
        raw_key = "wrk_expert:v1"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)

        assert result == "staff_wrk_expert:v1"


class TestEdgeCases:
    """边界情况测试"""

    def test_empty_raw_keys(self):
        """空输入"""
        canonicalizer = ProfileKeyCanonicalizer()
        result = canonicalizer.canonicalize([], {"staff_test:default"})
        assert result == {}

    def test_empty_available_keys(self):
        """没有可用的 keys"""
        canonicalizer = ProfileKeyCanonicalizer()
        result = canonicalizer._canonicalize_single("test:default", set())
        assert result == "test:default"  # 保留原值

    def test_no_match_possible(self):
        """无法匹配"""
        canonicalizer = ProfileKeyCanonicalizer()

        available_keys = {
            "staff_completely_different:default",
        }

        raw_key = "wrk_test:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)

        # 无法匹配时保留原值
        assert result == raw_key

    def test_special_characters_in_worker_id(self):
        """worker_id 包含特殊字符"""
        canonicalizer = ProfileKeyCanonicalizer()

        available_keys = {
            "staff_wrk_test_reg_v2_expert:default",
        }

        raw_key = "wrk_test_reg_v2_expert:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)

        assert result == "staff_wrk_test_reg_v2_expert:default"


class TestPrefixVariations:
    """前缀变化测试"""

    def test_wrk_prefix_matching(self):
        """wrk_ 前缀匹配"""
        canonicalizer = ProfileKeyCanonicalizer()

        available_keys = {"staff_wrk_test:default"}
        raw_key = "wrk_test:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)
        assert result == "staff_wrk_test:default"

    def test_staff_prefix_matching(self):
        """staff_ 前缀匹配"""
        canonicalizer = ProfileKeyCanonicalizer()

        available_keys = {"wrk_test:default"}
        raw_key = "staff_test:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)
        assert result == "wrk_test:default"

    def test_bot_prefix_matching(self):
        """bot_ 前缀匹配"""
        canonicalizer = ProfileKeyCanonicalizer()

        available_keys = {"staff_bot_test:default"}
        raw_key = "bot_test:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)
        assert result == "staff_bot_test:default"

    def test_nested_prefix_staff_wrk(self):
        """嵌套前缀 staff_wrk_"""
        canonicalizer = ProfileKeyCanonicalizer()

        available_keys = {"staff_wrk_test:default"}
        raw_key = "test:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)
        # 应该能通过添加前缀匹配
        assert result in available_keys