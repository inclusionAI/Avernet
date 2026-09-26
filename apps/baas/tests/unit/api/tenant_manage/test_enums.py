"""Unit tests for TenantType enum — K8S member addition (Phase 02 Plan 01).

Covers:
- FND-03: TenantType.K8S exists with value "K8S"
- Case-insensitive routing compatibility via .value.upper()
"""

from secbaas.community.api.tenant_manage import TenantType


class TestTenantTypeK8S:
    """Tests for the K8S member of TenantType (Phase 02 Plan 01 addition)."""

    def test_k8s_member_exists(self):
        """THEN K8S is a valid TenantType member."""
        assert hasattr(TenantType, "K8S"), "TenantType.K8S must exist"
        member = TenantType.K8S
        assert isinstance(member, TenantType), "K8S must be a TenantType instance"

    def test_k8s_value_is_uppercase(self):
        """THEN TenantType.K8S.value equals \"K8S\" (all-uppercase per D-01)."""
        assert TenantType.K8S.value == "K8S", (
            f"Expected 'K8S', got '{TenantType.K8S.value}'"
        )

    def test_k8s_value_upper_for_routing(self):
        """THEN TenantType.K8S.value.upper() equals \"K8S\" — verifies
        case-insensitive factory routing will work (Phase 02 Plan 02)."""
        assert TenantType.K8S.value.upper() == "K8S", (
            "value.upper() must normalize to 'K8S' for factory routing"
        )
