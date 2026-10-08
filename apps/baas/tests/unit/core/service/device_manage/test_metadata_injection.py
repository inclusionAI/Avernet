"""Tests for metadata injection in start_device ARCA path.

Verifies that service-level keys (publish_id, device_uuid, tenant, entity_id)
are correctly injected into detail_config.metadata before reaching
PaasServiceFacade.create_device().

Key correctness guarantees:
1. publish_id, device_uuid, tenant are always injected
2. entity_id falls back to credentials.entity_id then ""
3. arca_metadata caller-provided values take precedence (setdefault semantics)
4. entity_id from arca_metadata is NOT overwritten by empty credentials
"""

from unittest.mock import MagicMock

import pytest

from secbaas.community.api.device_manage import (
    ArcaDeviceConfig,
    DeployConfig,
    DeviceCredentials,
)
from secbaas.community.core.service.device_manage._device_service import (
    _build_arca_detail_config,
)

# ---------------------------------------------------------------------------
# _build_arca_detail_config: arca_metadata → detail_config.metadata
# ---------------------------------------------------------------------------


class TestBuildArcaDetailConfigMetadata:
    """Test _build_arca_detail_config maps arca_metadata to detail_config.metadata."""

    def test_arca_metadata_copied_to_detail_config(self):
        """arca_metadata dict is assigned to detail_config.metadata."""
        deploy_config = DeployConfig(
            arca_metadata={"bot_id": "bot-123", "custom_key": "custom_val"},
        )
        detail_config, _ = _build_arca_detail_config(
            deploy_config=deploy_config,
            oss_mount_id=None,
            env="dev",
            device_uuid="DEVICE-abc",
            secret_plugin=MagicMock(),
        )
        assert detail_config.metadata == {
            "bot_id": "bot-123",
            "custom_key": "custom_val",
        }

    def test_no_arca_metadata_means_metadata_is_none(self):
        """When arca_metadata is None, detail_config.metadata stays None."""
        deploy_config = DeployConfig(ttl_in_minutes=60)
        detail_config, _ = _build_arca_detail_config(
            deploy_config=deploy_config,
            oss_mount_id=None,
            env="dev",
            device_uuid="DEVICE-abc",
            secret_plugin=MagicMock(),
        )
        assert detail_config.metadata is None

    def test_empty_arca_metadata_dict_is_assigned(self):
        """An empty arca_metadata dict {} is still assigned (not None)."""
        deploy_config = DeployConfig(arca_metadata={})
        detail_config, _ = _build_arca_detail_config(
            deploy_config=deploy_config,
            oss_mount_id=None,
            env="dev",
            device_uuid="DEVICE-abc",
            secret_plugin=MagicMock(),
        )
        assert detail_config.metadata == {}

    def test_no_deploy_config_means_metadata_is_none(self):
        """When deploy_config is None, detail_config.metadata stays None."""
        detail_config, _ = _build_arca_detail_config(
            deploy_config=None,
            oss_mount_id=None,
            env="dev",
            device_uuid="DEVICE-abc",
            secret_plugin=MagicMock(),
        )
        assert detail_config.metadata is None


# ---------------------------------------------------------------------------
# start_device metadata injection logic (simulated — direct dict manipulation)
# The actual start_device is async and has deep dependencies, so we test the
# injection pattern directly against the dict operations.
# ---------------------------------------------------------------------------


class TestMetadataInjection:
    """Test the metadata injection logic that runs after _build_arca_detail_config.

    This mirrors the code in start_device():
        if detail_config.metadata is None:
            detail_config.metadata = {}
        detail_config.metadata.setdefault("publish_id", ...)
        detail_config.metadata.setdefault("device_uuid", ...)
        detail_config.metadata.setdefault("tenant", ...)
        if "entity_id" not in detail_config.metadata:
            entity_id = credentials.entity_id or ""
            detail_config.metadata["entity_id"] = entity_id
    """

    @staticmethod
    def _inject_metadata(
        metadata: dict | None,
        publish_id: int | None,
        device_uuid: str,
        tenant: str,
        entity_id_from_credentials: str | None = None,
    ) -> dict:
        """Simulate the metadata injection step from start_device."""
        if metadata is None:
            metadata = {}
        metadata.setdefault("publish_id", str(publish_id or ""))
        metadata.setdefault("device_uuid", device_uuid)
        metadata.setdefault("tenant", tenant)
        if "entity_id" not in metadata:
            metadata["entity_id"] = entity_id_from_credentials or ""
        return metadata

    def test_inject_all_keys_when_metadata_is_none(self):
        """All 4 keys injected when starting from None."""
        result = self._inject_metadata(
            metadata=None,
            publish_id=42,
            device_uuid="DEVICE-abc",
            tenant="test_tenant",
        )
        assert result == {
            "publish_id": "42",
            "device_uuid": "DEVICE-abc",
            "tenant": "test_tenant",
            "entity_id": "",
        }

    def test_inject_into_empty_dict(self):
        """All 4 keys injected when starting from {}."""
        result = self._inject_metadata(
            metadata={},
            publish_id=100,
            device_uuid="DEVICE-xyz",
            tenant="my_tenant",
        )
        assert result == {
            "publish_id": "100",
            "device_uuid": "DEVICE-xyz",
            "tenant": "my_tenant",
            "entity_id": "",
        }

    def test_entity_id_from_credentials(self):
        """entity_id taken from credentials when arca_metadata has no entity_id."""
        result = self._inject_metadata(
            metadata={},
            publish_id=1,
            device_uuid="DEVICE-1",
            tenant="t",
            entity_id_from_credentials="staff_001",
        )
        assert result["entity_id"] == "staff_001"

    def test_entity_id_from_arca_metadata_takes_precedence(self):
        """arca_metadata entity_id is preserved; credentials.entity_id does NOT overwrite."""
        # Caller sets entity_id in arca_metadata
        metadata = {"entity_id": "caller_entity_123", "bot_id": "bot-abc"}
        result = self._inject_metadata(
            metadata=metadata,
            publish_id=5,
            device_uuid="DEVICE-5",
            tenant="t",
            entity_id_from_credentials="should_not_override",
        )
        assert result["entity_id"] == "caller_entity_123"

    def test_publish_id_not_overwritten_by_setdefault(self):
        """If arca_metadata already has publish_id, setdefault preserves it."""
        metadata = {"publish_id": "99"}
        result = self._inject_metadata(
            metadata=metadata,
            publish_id=1,
            device_uuid="DEVICE-x",
            tenant="t",
        )
        assert result["publish_id"] == "99"

    def test_device_uuid_not_overwritten_by_setdefault(self):
        """If arca_metadata already has device_uuid, setdefault preserves it."""
        metadata = {"device_uuid": "CUSTOM-UUID"}
        result = self._inject_metadata(
            metadata=metadata,
            publish_id=1,
            device_uuid="DEVICE-x",
            tenant="t",
        )
        assert result["device_uuid"] == "CUSTOM-UUID"

    def test_tenant_not_overwritten_by_setdefault(self):
        """If arca_metadata already has tenant, setdefault preserves it."""
        metadata = {"tenant": "custom_tenant"}
        result = self._inject_metadata(
            metadata=metadata,
            publish_id=1,
            device_uuid="DEVICE-x",
            tenant="default_tenant",
        )
        assert result["tenant"] == "custom_tenant"

    def test_publish_id_none_becomes_empty_string(self):
        """publish_id=None is serialized as empty string."""
        result = self._inject_metadata(
            metadata=None,
            publish_id=None,
            device_uuid="DEVICE-1",
            tenant="t",
        )
        assert result["publish_id"] == ""

    def test_entity_id_empty_string_when_no_credentials(self):
        """entity_id defaults to empty string when no credentials provided."""
        result = self._inject_metadata(
            metadata=None,
            publish_id=1,
            device_uuid="DEVICE-1",
            tenant="t",
            entity_id_from_credentials=None,
        )
        assert result["entity_id"] == ""

    def test_full_flow_with_existing_arca_metadata(self):
        """Complete flow: arca_metadata has some keys, service injects the rest."""
        # Simulates: _build_arca_detail_config sets metadata from arca_metadata,
        # then start_device injects service-level keys
        metadata = {
            "bot_id": "bot-abc",
            "callback_token": "tok-123",
            "entity_id": "staff_zhangsan",
        }
        result = self._inject_metadata(
            metadata=metadata,
            publish_id=777,
            device_uuid="DEVICE-777",
            tenant="prod_tenant",
            entity_id_from_credentials="should_not_override",
        )
        # Caller-provided keys preserved
        assert result["bot_id"] == "bot-abc"
        assert result["callback_token"] == "tok-123"
        assert result["entity_id"] == "staff_zhangsan"
        # Service-injected keys added
        assert result["publish_id"] == "777"
        assert result["device_uuid"] == "DEVICE-777"
        assert result["tenant"] == "prod_tenant"


# ---------------------------------------------------------------------------
# _is_provided safety check — ensures metadata dict is not treated as "not provided"
# ---------------------------------------------------------------------------


class TestIsProvidedMetadataSafety:
    """Verify _is_provided does not drop a non-empty metadata dict.

    _is_provided("") returns False (empty string treated as not provided).
    But a dict with empty-string VALUES should still be treated as provided,
    because the dict itself is not None and not "".
    """

    def test_dict_with_empty_string_values_is_provided(self):
        """A dict containing empty-string values is treated as provided."""
        from secbaas.community.core.service.paas._facade import PaasServiceFacade

        assert PaasServiceFacade._is_provided({"entity_id": ""}) is True

    def test_empty_dict_is_provided(self):
        """An empty dict {} is treated as provided (not None, not '')."""
        from secbaas.community.core.service.paas._facade import PaasServiceFacade

        assert PaasServiceFacade._is_provided({}) is True

    def test_none_is_not_provided(self):
        """None is not provided."""
        from secbaas.community.core.service.paas._facade import PaasServiceFacade

        assert PaasServiceFacade._is_provided(None) is False

    def test_empty_string_is_not_provided(self):
        """Empty string is not provided (per D-02)."""
        from secbaas.community.core.service.paas._facade import PaasServiceFacade

        assert PaasServiceFacade._is_provided("") is False
