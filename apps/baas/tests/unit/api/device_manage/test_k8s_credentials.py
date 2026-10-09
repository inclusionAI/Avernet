"""Unit tests for K8sCredentials model.

Tests cover:
- Model construction with all fields
- Inheritance from PaasCredentials (template_id, template_uuid, tenant_name)
- is_configured() behavior (kubeconfig content non-empty check)
- model_config extra="forbid" behavior
- Default values for optional fields
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from secbaas.community.api.device_manage import (
    K8sCredentials,
    PaasCredentials,
)

_TEMPLATE_UUID = "tpl-uuid-1"


class TestK8sCredentialsInheritance:
    """Verify K8sCredentials inherits from PaasCredentials."""

    def test_inherits_paas_credentials(self):
        """K8sCredentials must be a subclass of PaasCredentials."""
        assert issubclass(K8sCredentials, PaasCredentials)

    def test_inherits_template_id(self):
        """template_id should be inherited from PaasCredentials."""
        creds = K8sCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
        )
        assert creds.template_id == 1

    def test_inherits_template_uuid(self):
        """template_uuid should be inherited from PaasCredentials."""
        creds = K8sCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
        )
        assert creds.template_uuid == _TEMPLATE_UUID

    def test_inherits_tenant_name(self):
        """tenant_name should be inherited from PaasCredentials."""
        creds = K8sCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            tenant_name="default",
        )
        assert creds.tenant_name == "default"


class TestK8sCredentialsDefaults:
    """Verify K8sCredentials defaults for optional fields."""

    def test_all_fields_default_to_none_or_empty(self):
        """All K8s-specific fields must have sensible defaults."""
        creds = K8sCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
        )
        assert creds.kubeconfig is None
        assert creds.context is None
        assert creds.namespace is None
        assert creds.image is None
        assert creds.cpu_request is None
        assert creds.cpu_limit is None
        assert creds.memory_request is None
        assert creds.memory_limit is None
        assert creds.extra_k8s_opts == {}

    def test_extra_k8s_opts_default_is_empty_dict(self):
        """extra_k8s_opts must default to empty dict."""
        creds = K8sCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
        )
        assert creds.extra_k8s_opts == {}
        assert isinstance(creds.extra_k8s_opts, dict)


class TestK8sCredentialsConstruction:
    """Verify K8sCredentials construction with all fields."""

    def test_construction_with_all_fields(self):
        """All fields should be accepted and stored correctly."""
        creds = K8sCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            tenant_name="default",
            kubeconfig="/path/to/kubeconfig",
            context="my-context",
            namespace="my-ns",
            image="registry.example.com/bot:latest",
            cpu_request="500m",
            cpu_limit="1",
            memory_request="512Mi",
            memory_limit="1Gi",
            extra_k8s_opts={"nodeSelector": {"disktype": "ssd"}},
        )
        assert creds.kubeconfig == "/path/to/kubeconfig"
        assert creds.context == "my-context"
        assert creds.namespace == "my-ns"
        assert creds.image == "registry.example.com/bot:latest"
        assert creds.cpu_request == "500m"
        assert creds.cpu_limit == "1"
        assert creds.memory_request == "512Mi"
        assert creds.memory_limit == "1Gi"
        assert creds.extra_k8s_opts == {"nodeSelector": {"disktype": "ssd"}}


class TestK8sCredentialsIsConfigured:
    """Verify is_configured() behavior."""

    def test_is_configured_with_valid_kubeconfig(self):
        """is_configured() returns True when kubeconfig content is non-empty."""
        creds = K8sCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            kubeconfig="apiVersion: v1\nkind: Config\n",
        )
        assert creds.is_configured() is True

    def test_is_configured_with_none_path(self):
        """is_configured() returns False when kubeconfig is None."""
        creds = K8sCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
        )
        assert creds.is_configured() is False

    def test_is_configured_with_nonexistent_path(self):
        """is_configured() returns True for any non-empty kubeconfig string.

        Since is_configured() only checks bool(kubeconfig), even a string that
        is not a valid file path returns True as long as it's non-empty.
        """
        creds = K8sCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            kubeconfig="/nonexistent/path/kubeconfig.yaml",
        )
        assert creds.is_configured() is True

    def test_is_configured_with_empty_string_path(self):
        """is_configured() returns False when kubeconfig is empty string."""
        creds = K8sCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            kubeconfig="",
        )
        assert creds.is_configured() is False

    def test_is_configured_only_checks_kubeconfig(self):
        """is_configured() only checks kubeconfig content, not other fields."""
        creds = K8sCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            kubeconfig="some-kubeconfig-content",
            namespace=None,
            image=None,
        )
        assert creds.is_configured() is True


class TestK8sCredentialsExtraForbid:
    """Verify model_config extra="forbid" behavior."""

    def test_unknown_fields_rejected(self):
        """K8sCredentials must reject unknown fields due to extra='forbid'."""
        with pytest.raises(ValidationError):
            K8sCredentials(
                template_id=1,
                template_uuid=_TEMPLATE_UUID,
                unknown_field="should_fail",
            )

    def test_extra_k8s_opts_still_works(self):
        """extra_k8s_opts is a defined field, so it must still work with extra='forbid'."""
        creds = K8sCredentials(
            template_id=1,
            template_uuid=_TEMPLATE_UUID,
            extra_k8s_opts={"tolerations": [{"key": "dedicated"}]},
        )
        assert creds.extra_k8s_opts == {"tolerations": [{"key": "dedicated"}]}


class TestK8sCredentialsTypoPrevention:
    """Verify extra='forbid' catches common typos."""

    def test_kubeconfig_typo_rejected(self):
        """Typo in 'kubeconfig' field must raise ValidationError."""
        with pytest.raises(ValidationError):
            K8sCredentials(
                template_id=1,
                template_uuid=_TEMPLATE_UUID,
                kubeconfigPath="/path/to/kubeconfig",  # camelCase: NOT the field name
            )

    def test_misspelled_cpu_request_rejected(self):
        """Misspelled field name must raise ValidationError."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            K8sCredentials(
                template_id=1,
                template_uuid=_TEMPLATE_UUID,
                cpu_requst="500m",  # 'requst' not 'request'
            )
