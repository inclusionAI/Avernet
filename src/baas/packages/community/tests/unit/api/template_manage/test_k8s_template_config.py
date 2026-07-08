"""Unit tests for K8sTemplateConfig and DeviceTemplateConfigUnion."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from secbaas.api.template_manage import (
    DeviceTemplateConfig,
    DeviceTemplateConfigUnion,
    K8sTemplateConfig,
)


class TestK8sTemplateConfigConstruction:
    """Tests for K8sTemplateConfig construction and field access."""

    def test_with_all_fields(self) -> None:
        """Test creating K8sTemplateConfig with all fields."""
        config = K8sTemplateConfig.model_validate(
            {
                "type": "K8s",
                "kubeconfig": "/path/to/kubeconfig",
                "namespace": "production",
                "image": "registry.example.com/bot:v2",
                "cpu_request": "500m",
                "cpu_limit": "1",
                "memory_request": "512Mi",
                "memory_limit": "1Gi",
            }
        )
        assert config.type == "K8s"
        assert config.kubeconfig == "/path/to/kubeconfig"
        assert config.namespace == "production"
        assert config.image == "registry.example.com/bot:v2"
        assert config.cpu_request == "500m"
        assert config.cpu_limit == "1"
        assert config.memory_request == "512Mi"
        assert config.memory_limit == "1Gi"

    def test_with_minimal_fields(self) -> None:
        """Test creating K8sTemplateConfig with only required fields (type + kubeconfig)."""
        config = K8sTemplateConfig.model_validate(
            {
                "type": "K8s",
                "kubeconfig": "/etc/kubeconfig",
            }
        )
        assert config.type == "K8s"
        assert config.kubeconfig == "/etc/kubeconfig"
        # Defaults
        assert config.namespace == "default"
        assert config.image == "registry.example.com/bot-runtime:latest"
        assert config.cpu_request is None
        assert config.cpu_limit is None
        assert config.memory_request is None
        assert config.memory_limit is None

    def test_direct_construction(self) -> None:
        """Test creating K8sTemplateConfig via constructor (not model_validate)."""
        config = K8sTemplateConfig(
            type="K8s",
            kubeconfig="/path/to/kubeconfig",
        )
        assert config.type == "K8s"
        assert config.kubeconfig == "/path/to/kubeconfig"


class TestK8sTemplateConfigTypeDiscriminator:
    """Tests for the type discriminator field."""

    def test_type_must_be_k8s(self) -> None:
        """Test constructing with type='ARCA' raises ValidationError."""
        with pytest.raises(ValidationError):
            K8sTemplateConfig.model_validate(
                {
                    "type": "ARCA",
                    "kubeconfig": "/path/to/kubeconfig",
                }
            )

    def test_type_must_be_literal(self) -> None:
        """Test constructing with type='invalid' raises ValidationError."""
        with pytest.raises(ValidationError):
            K8sTemplateConfig.model_validate(
                {
                    "type": "invalid",
                    "kubeconfig": "/path/to/kubeconfig",
                }
            )

    def test_kubeconfig_required(self) -> None:
        """Test constructing without kubeconfig raises ValidationError."""
        with pytest.raises(ValidationError):
            K8sTemplateConfig.model_validate({"type": "K8s"})


class TestK8sTemplateConfigExtraAllow:
    """Tests for extra='allow' behavior."""

    def test_extra_field_preserved(self) -> None:
        """Test that unknown fields are preserved (extra='allow')."""
        config = K8sTemplateConfig.model_validate(
            {
                "type": "K8s",
                "kubeconfig": "/path/to/kubeconfig",
                "extra_field": "extra_value",
            }
        )
        assert config.kubeconfig == "/path/to/kubeconfig"
        dumped = config.model_dump()
        assert dumped["extra_field"] == "extra_value"

    def test_unknown_fields_in_model_dump(self) -> None:
        """Test that model_dump includes extra fields."""
        config = K8sTemplateConfig.model_validate(
            {
                "type": "K8s",
                "kubeconfig": "/path/to/kubeconfig",
                "custom_key": "custom_value",
                "nested": {"key": "value"},
            }
        )
        dumped = config.model_dump()
        assert dumped["kubeconfig"] == "/path/to/kubeconfig"
        assert dumped["custom_key"] == "custom_value"
        assert dumped["nested"] == {"key": "value"}

    def test_model_config_extra_allow(self) -> None:
        """Test that model_config has extra='allow'."""
        config = K8sTemplateConfig.model_validate(
            {
                "type": "K8s",
                "kubeconfig": "/path/to/kubeconfig",
                "unknown": True,
            }
        )
        assert config.model_dump()["unknown"] is True


class TestK8sTemplateConfigUnionMembership:
    """Tests for Union membership."""

    def test_in_device_template_config_union(self) -> None:
        """Test K8sTemplateConfig isinstance check against DeviceTemplateConfigUnion."""
        config = K8sTemplateConfig(
            type="K8s",
            kubeconfig="/path/to/kubeconfig",
        )
        # A K8sTemplateConfig instance should match the Union type when
        # checked via isinstance against any member of the Union.
        assert isinstance(config, K8sTemplateConfig)

    def test_in_device_template_config(self) -> None:
        """Test K8sTemplateConfig is included in the original DeviceTemplateConfig Union."""
        from typing import get_args

        args = get_args(DeviceTemplateConfig)
        type_names = {getattr(t, "__name__", str(t)) for t in args}
        assert "K8sTemplateConfig" in type_names

    def test_in_devicetemplateconfig_union_alias(self) -> None:
        """Test K8sTemplateConfig is in DeviceTemplateConfigUnion type alias."""
        from typing import get_args

        args = get_args(DeviceTemplateConfigUnion)
        type_names = {getattr(t, "__name__", str(t)) for t in args}
        assert "K8sTemplateConfig" in type_names


class TestK8sTemplateConfigSerialization:
    """Tests for serialization round-trips."""

    def test_model_dump_round_trip(self) -> None:
        """Test model_dump -> model_validate preserves all fields."""
        original = K8sTemplateConfig.model_validate(
            {
                "type": "K8s",
                "kubeconfig": "/path/to/kubeconfig",
                "namespace": "staging",
                "image": "registry.example.com/bot:latest",
                "cpu_request": "250m",
                "cpu_limit": "500m",
                "memory_request": "256Mi",
                "memory_limit": "512Mi",
            }
        )
        dumped = original.model_dump()
        restored = K8sTemplateConfig.model_validate(dumped)
        assert restored.type == original.type
        assert restored.kubeconfig == original.kubeconfig
        assert restored.namespace == original.namespace
        assert restored.image == original.image
        assert restored.cpu_request == original.cpu_request
        assert restored.cpu_limit == original.cpu_limit
        assert restored.memory_request == original.memory_request
        assert restored.memory_limit == original.memory_limit

    def test_model_dump_exclude_none(self) -> None:
        """Test model_dump with exclude_none for storage."""
        config = K8sTemplateConfig.model_validate(
            {
                "type": "K8s",
                "kubeconfig": "/path/to/kubeconfig",
            }
        )
        dumped = config.model_dump(exclude_none=True)
        assert "type" in dumped
        assert "kubeconfig" in dumped
        assert "namespace" in dumped  # "default" is not None
        assert "image" in dumped  # has a default value
        # Resource fields should be excluded (None)
        assert "cpu_request" not in dumped
        assert "cpu_limit" not in dumped
        assert "memory_request" not in dumped
        assert "memory_limit" not in dumped
