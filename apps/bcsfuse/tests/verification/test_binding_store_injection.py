"""
Binding Store 注入验证测试

这些测试验证 binding_store 是否正确注入到整个调用链中。

关键验证点：
1. binding_store 是否正确注入到 WorkerProfileRetrievalService
2. binding_store 是否被 canonicalizer 正确使用
3. 不同格式的 binding 是否能正确匹配
"""

import pytest
from unittest.mock import MagicMock, patch


class TestBindingStoreInjection:
    """验证 binding_store 注入"""


    def test_binding_store_used_in_canonicalization(self):
        """
        验证：canonicalization 过程中必须使用 binding_store
        """
        from app.domain.services.profile_key_canonicalizer import ProfileKeyCanonicalizer

        # 模拟 binding
        mock_binding = MagicMock()
        mock_binding.worker_id = "test_worker"
        mock_binding.profile_key = "test_worker:default"

        mock_binding_store = MagicMock()
        mock_binding_store.get_binding_by_profile_key.return_value = mock_binding

        canonicalizer = ProfileKeyCanonicalizer(binding_store=mock_binding_store)

        # 调用 canonicalize
        available_keys = {"staff_test_worker:default"}
        result = canonicalizer._canonicalize_single("test_worker:default", available_keys)

        # 验证 binding_store 被调用
        mock_binding_store.get_binding_by_profile_key.assert_called()



class TestBindingStoreFormatHandling:
    """验证 binding store 处理不同格式"""

    def test_api_binding_format_to_profile_source_format(self):
        """
        验证：API binding 格式转换为 Profile Source 格式

        API binding: wrk_test:default
        Profile Source: staff_wrk_test:default
        """
        from app.domain.services.profile_key_canonicalizer import ProfileKeyCanonicalizer

        mock_binding = MagicMock()
        mock_binding.worker_id = "wrk_test"
        mock_binding.profile_key = "wrk_test:default"

        mock_binding_store = MagicMock()
        mock_binding_store.get_binding_by_profile_key.return_value = mock_binding

        canonicalizer = ProfileKeyCanonicalizer(binding_store=mock_binding_store)

        # Profile Source 格式
        available_keys = {"staff_wrk_test:default"}

        # API 格式请求
        result = canonicalizer._canonicalize_single("wrk_test:default", available_keys)

        # 必须转换为 staff_ 格式
        assert result == "staff_wrk_test:default"

    def test_file_binding_format_to_profile_source_format(self):
        """
        验证：FILE binding 格式与 Profile Source 格式一致

        FILE binding: staff_wrk_test:default
        Profile Source: staff_wrk_test:default
        """
        from app.domain.services.profile_key_canonicalizer import ProfileKeyCanonicalizer

        mock_binding = MagicMock()
        mock_binding.worker_id = "wrk_test"
        mock_binding.profile_key = "staff_wrk_test:default"

        mock_binding_store = MagicMock()
        mock_binding_store.get_binding_by_profile_key.return_value = mock_binding

        canonicalizer = ProfileKeyCanonicalizer(binding_store=mock_binding_store)

        available_keys = {"staff_wrk_test:default"}

        result = canonicalizer._canonicalize_single("staff_wrk_test:default", available_keys)

        assert result == "staff_wrk_test:default"





class TestBindingStoreNotFound:
    """验证 binding store 找不到时的行为"""

    def test_binding_not_found_fallback_to_prefix_match(self):
        """
        验证：binding 找不到时，回退到前缀匹配

        这是合理的 fallback 行为
        """
        from app.domain.services.profile_key_canonicalizer import ProfileKeyCanonicalizer

        mock_binding_store = MagicMock()
        mock_binding_store.get_binding_by_profile_key.return_value = None

        canonicalizer = ProfileKeyCanonicalizer(binding_store=mock_binding_store)

        available_keys = {"staff_wrk_test:default"}
        result = canonicalizer._canonicalize_single("wrk_test:default", available_keys)

        # 应该通过前缀添加匹配
        assert result == "staff_wrk_test:default"

    def test_binding_not_found_and_no_prefix_match(self):
        """
        验证：binding 找不到且前缀也不匹配时，保留原值
        """
        from app.domain.services.profile_key_canonicalizer import ProfileKeyCanonicalizer

        mock_binding_store = MagicMock()
        mock_binding_store.get_binding_by_profile_key.return_value = None

        canonicalizer = ProfileKeyCanonicalizer(binding_store=mock_binding_store)

        available_keys = {"staff_completely_different:default"}
        raw_key = "wrk_test:default"

        result = canonicalizer._canonicalize_single(raw_key, available_keys)

        # 保留原值
        assert result == raw_key