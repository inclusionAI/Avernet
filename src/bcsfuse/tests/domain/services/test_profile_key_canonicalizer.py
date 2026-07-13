"""
Unit tests for ProfileKeyCanonicalizer

Tests profile key normalization and matching strategies.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.domain.services.profile_key_canonicalizer import ProfileKeyCanonicalizer


class TestProfileKeyCanonicalizer:
    """Tests for ProfileKeyCanonicalizer"""

    def setup_method(self):
        """Setup test fixtures"""
        self.canonicalizer = ProfileKeyCanonicalizer()
        self.available_keys = {
            "staff_wrk_test_reg_g1_architect:default",
            "staff_wrk_test_reg_g2_security_expert:default",
            "wrk_test_reg_g5_audit_expert:default",
            "bot_test_reg_g1_architect:v1",
        }

    # =========================================================================
    # Basic Canonicalization Tests
    # =========================================================================

    def test_canonicalize_empty_keys(self):
        """Should return empty dict for empty input"""
        result = self.canonicalizer.canonicalize([])
        assert result == {}

    def test_canonicalize_exact_match(self):
        """Should return original key when exact match exists"""
        raw_keys = ["staff_wrk_test_reg_g1_architect:default"]
        result = self.canonicalizer.canonicalize(raw_keys, self.available_keys)
        assert result[raw_keys[0]] == raw_keys[0]

    def test_canonicalize_no_available_keys(self):
        """Should return original keys when no available_keys provided"""
        raw_keys = ["wrk_test_reg_g1_architect:default"]
        result = self.canonicalizer.canonicalize(raw_keys)
        assert result[raw_keys[0]] == raw_keys[0]

    # =========================================================================
    # Prefix Matching Tests
    # =========================================================================

    def test_canonicalize_prefix_wrk_to_staff(self):
        """Should match wrk_* prefix to staff_* prefix"""
        raw_keys = ["wrk_test_reg_g1_architect:default"]
        result = self.canonicalizer.canonicalize(raw_keys, self.available_keys)
        assert result[raw_keys[0]] == "staff_wrk_test_reg_g1_architect:default"

    def test_canonicalize_prefix_staff_to_wrk(self):
        """Should match staff_* prefix to wrk_* prefix"""
        available = {
            "wrk_test_reg_g1_architect:default",
        }
        raw_keys = ["staff_test_reg_g1_architect:default"]
        result = self.canonicalizer.canonicalize(raw_keys, available)
        assert result[raw_keys[0]] == "wrk_test_reg_g1_architect:default"

    def test_canonicalize_prefix_bot_match(self):
        """Should match bot_* prefix correctly"""
        raw_keys = ["test_reg_g1_architect:v1"]
        result = self.canonicalizer.canonicalize(raw_keys, self.available_keys)
        # should match bot_test_reg_g1_architect:v1
        assert result[raw_keys[0]] == "bot_test_reg_g1_architect:v1"

    # =========================================================================
    # Core ID Matching Tests
    # =========================================================================

    def test_canonicalize_core_id_match(self):
        """Should match based on core ID (ignoring prefix differences)"""
        # Profile key format: "prefix_core_id:profile_id"
        # E.g., wrk_test_reg_g5_audit_expert:default should match
        # when available_keys has staff_wrk_test_reg_g5_audit_expert:default
        available = {
            "staff_wrk_test_reg_g5_audit_expert:default",
        }
        raw_keys = ["wrk_test_reg_g5_audit_expert:default"]
        result = self.canonicalizer.canonicalize(raw_keys, available)
        assert result[raw_keys[0]] == "staff_wrk_test_reg_g5_audit_expert:default"

    def test_canonicalize_core_id_with_different_profile_id(self):
        """Should NOT match when profile_id differs"""
        available = {
            "staff_wrk_test_reg_g1_architect:v2",
        }
        raw_keys = ["wrk_test_reg_g1_architect:default"]
        result = self.canonicalizer.canonicalize(raw_keys, available)
        # Should not find a match, returns original
        assert result[raw_keys[0]] == raw_keys[0]

    # =========================================================================
    # Binding Store Tests
    # =========================================================================

    def test_canonicalize_with_binding_store(self):
        """Should prioritize binding store lookup when available"""
        # Create mock binding store
        mock_binding = MagicMock()
        mock_binding.profile_key = "staff_wrk_test_reg_g1_architect:default"

        mock_binding_store = MagicMock()
        mock_binding_store.get_binding_by_profile_key.return_value = mock_binding

        canonicalizer = ProfileKeyCanonicalizer(binding_store=mock_binding_store)

        raw_keys = ["some_unknown_format:default"]
        available = {
            "staff_wrk_test_reg_g1_architect:default",
        }
        result = canonicalizer.canonicalize(raw_keys, available)

        # Should use binding store result
        assert result[raw_keys[0]] == "staff_wrk_test_reg_g1_architect:default"
        mock_binding_store.get_binding_by_profile_key.assert_called_once_with(raw_keys[0])

    def test_canonicalize_binding_store_no_binding(self):
        """Should fallback to prefix matching when binding store returns None"""
        mock_binding_store = MagicMock()
        mock_binding_store.get_binding_by_profile_key.return_value = None

        canonicalizer = ProfileKeyCanonicalizer(binding_store=mock_binding_store)

        raw_keys = ["wrk_test_reg_g1_architect:default"]
        result = canonicalizer.canonicalize(raw_keys, self.available_keys)

        # Should fallback to prefix matching
        assert result[raw_keys[0]] == "staff_wrk_test_reg_g1_architect:default"

    # =========================================================================
    # Helper Method Tests
    # =========================================================================

    def test_extract_worker_id(self):
        """Should correctly extract worker_id from profile_key"""
        assert self.canonicalizer.extract_worker_id("staff_wrk_test:default") == "staff_wrk_test"
        assert self.canonicalizer.extract_worker_id("wrk_test:v1") == "wrk_test"
        assert self.canonicalizer.extract_worker_id("simple") == "simple"

    def test_extract_worker_id_empty(self):
        """Should return None for empty input"""
        assert self.canonicalizer.extract_worker_id("") is None
        assert self.canonicalizer.extract_worker_id(None) is None

    def test_extract_profile_id(self):
        """Should correctly extract profile_id from profile_key"""
        assert self.canonicalizer.extract_profile_id("staff_wrk_test:default") == "default"
        assert self.canonicalizer.extract_profile_id("wrk_test:v1") == "v1"

    def test_extract_profile_id_default(self):
        """Should return 'default' when no profile_id present"""
        assert self.canonicalizer.extract_profile_id("staff_wrk_test") == "default"
        assert self.canonicalizer.extract_profile_id("") == "default"

    # =========================================================================
    # Edge Cases
    # =========================================================================

    def test_canonicalize_multiple_keys(self):
        """Should handle multiple keys in one call"""
        raw_keys = [
            "wrk_test_reg_g1_architect:default",
            "wrk_test_reg_g2_security_expert:default",
            "wrk_test_reg_g5_audit_expert:default",  # already in available format
        ]
        result = self.canonicalizer.canonicalize(raw_keys, self.available_keys)

        assert result["wrk_test_reg_g1_architect:default"] == "staff_wrk_test_reg_g1_architect:default"
        assert result["wrk_test_reg_g2_security_expert:default"] == "staff_wrk_test_reg_g2_security_expert:default"
        # Note: wrk_test_reg_g5_audit_expert:default is directly in available_keys
        assert result["wrk_test_reg_g5_audit_expert:default"] == "wrk_test_reg_g5_audit_expert:default"

    def test_canonicalize_no_match(self):
        """Should return original key when no match found"""
        raw_keys = ["completely_unknown_key:default"]
        result = self.canonicalizer.canonicalize(raw_keys, self.available_keys)
        assert result[raw_keys[0]] == raw_keys[0]

    def test_canonicalize_complex_worker_id(self):
        """Should handle complex worker IDs with multiple underscores"""
        available = {
            "staff_wrk_test_reg_g5_security_audit_expert:default",
        }
        raw_keys = ["wrk_test_reg_g5_security_audit_expert:default"]
        result = self.canonicalizer.canonicalize(raw_keys, available)
        assert result[raw_keys[0]] == "staff_wrk_test_reg_g5_security_audit_expert:default"


class TestProfileKeyCanonicalizerPrefixVariations:
    """Tests for various prefix combinations"""

    def setup_method(self):
        self.canonicalizer = ProfileKeyCanonicalizer()

    @pytest.mark.parametrize("raw_prefix,available_prefix,expected_match", [
        ("wrk_", "staff_", True),
        ("staff_", "wrk_", True),
        ("bot_", "wrk_", True),
        ("wrk_", "bot_", True),
        ("staff_", "bot_", True),
        ("bot_", "staff_", True),
    ])
    def test_prefix_variations(self, raw_prefix, available_prefix, expected_match):
        """Test all prefix variation combinations"""
        core_id = "test_reg_g1_architect"
        profile_id = "default"

        raw_key = f"{raw_prefix}{core_id}:{profile_id}"
        available_keys = {f"{available_prefix}{core_id}:{profile_id}"}

        result = self.canonicalizer.canonicalize([raw_key], available_keys)

        if expected_match:
            assert result[raw_key] == f"{available_prefix}{core_id}:{profile_id}"
        else:
            # This test assumes all combinations should match
            assert result[raw_key] in available_keys or result[raw_key] == raw_key


class TestProfileKeyCanonicalizerRealWorldScenarios:
    """Tests based on real-world scenarios from prepub logs"""

    def setup_method(self):
        self.canonicalizer = ProfileKeyCanonicalizer()

    def test_scenario_from_prepub_logs(self):
        """
        Scenario from prepub logs:
        - request.participants: wrk_test_reg_g5_security_audit_expert:default
        - scanned profile_key: staff_wrk_test_reg_g1_architect:default

        This test simulates the participant key needing to match against available keys.
        """
        # Simulating available profile keys from scan
        available_keys = {
            "staff_wrk_test_reg_g1_architect:default",
            "staff_wrk_test_reg_g2_ops_expert:default",
            "staff_wrk_test_reg_g3_dba_expert:default",
            "staff_wrk_test_reg_g5_security_audit_expert:default",  # This should match
        }

        # Requested participant
        raw_keys = ["wrk_test_reg_g5_security_audit_expert:default"]

        result = self.canonicalizer.canonicalize(raw_keys, available_keys)

        assert result[raw_keys[0]] == "staff_wrk_test_reg_g5_security_audit_expert:default"

    def test_scenario_binding_store_format_mismatch(self):
        """
        关键场景：Binding 存储格式与 Profile Source 格式不同

        Background:
        - Binding store 中 profile_key 格式: {worker_id}:{profile_id} (wrk_xxx:default)
        - Profile Source 扫描的 profile_key 格式: staff_{staff_id}:{profile_id} (staff_wrk_xxx:default)

        当通过 binding store 查找时，需要正确转换格式。
        """
        # 模拟 binding store 返回 binding
        mock_binding = MagicMock()
        mock_binding.worker_id = "wrk_test_reg_g5_expert"
        mock_binding.profile_key = "wrk_test_reg_g5_expert:default"  # Binding 存储的格式

        mock_binding_store = MagicMock()
        mock_binding_store.get_binding_by_profile_key.return_value = mock_binding

        canonicalizer = ProfileKeyCanonicalizer(binding_store=mock_binding_store)

        # Profile Source 扫描出的实际格式
        available_keys = {
            "staff_wrk_test_reg_g5_expert:default",  #Profile Source 格式
        }

        # 请求中的 participants 格式 (与 binding 格式一致)
        raw_keys = ["wrk_test_reg_g5_expert:default"]

        result = canonicalizer.canonicalize(raw_keys, available_keys)

        # 应该正确匹配到 Profile Source 格式
        assert result[raw_keys[0]] == "staff_wrk_test_reg_g5_expert:default"

    def test_scenario_multiple_participants_mixed_formats(self):
        """
        Multiple participants with mixed format requirements.
        """
        available_keys = {
            "staff_wrk_g1_architect:default",
            "wrk_g2_security:default",
            "staff_g3_dba:default",  # Note: only staff_ prefix, not staff_wrk_
        }

        raw_keys = [
            "wrk_g1_architect:default",    # needs wrk -> staff_wrk prefix swap
            "staff_g2_security:default",   # needs staff -> wrk prefix swap
            "g3_dba:default",              # needs prefix addition (any prefix)
        ]

        result = self.canonicalizer.canonicalize(raw_keys, available_keys)

        assert result["wrk_g1_architect:default"] == "staff_wrk_g1_architect:default"
        assert result["staff_g2_security:default"] == "wrk_g2_security:default"
        # g3_dba:default should match staff_g3_dba:default by adding staff_ prefix
        assert result["g3_dba:default"] == "staff_g3_dba:default"