"""Unit tests for PaaSProviderType enum — K8S member addition (Phase 02 Plan 01).

Covers:
- FND-04: PaaSProviderType.K8S exists with value "K8S"
- Factory routing compatibility via .upper()
"""

from secbaas.community.api.health_check.paas import PaaSProviderType


class TestPaaSProviderTypeK8S:
    """Tests for the K8S member of PaaSProviderType (Phase 02 Plan 01 addition)."""

    def test_k8s_member_exists(self):
        """THEN K8S is a valid PaaSProviderType member."""
        assert hasattr(PaaSProviderType, "K8S"), "PaaSProviderType.K8S must exist"
        member = PaaSProviderType.K8S
        assert isinstance(member, PaaSProviderType), (
            "K8S must be a PaaSProviderType instance"
        )

    def test_k8s_value_is_uppercase(self):
        """THEN PaaSProviderType.K8S.value equals \"K8S\" (all-uppercase per D-01)."""
        assert PaaSProviderType.K8S.value == "K8S", (
            f"Expected 'K8S', got '{PaaSProviderType.K8S.value}'"
        )

    def test_k8s_upper_for_routing(self):
        """THEN PaaSProviderType.K8S.upper() equals \"K8S\" — verifies
        factory routing will work (Phase 02 Plan 03)."""
        assert PaaSProviderType.K8S.upper() == "K8S", (
            ".upper() must normalize to 'K8S' for provider factory routing"
        )
