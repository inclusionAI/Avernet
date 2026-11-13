"""Explicit local-development AppArmor policy; no live Kubernetes required."""
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client import ApiClient

from secbaas.community.plugins.sandbox.arca.local_k8s._config import _apparmor_profile_name
from secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin import LocalK8sArcaSandboxPlugin


@pytest.fixture(autouse=True)
def clean_policy(monkeypatch):
    monkeypatch.delenv("LOCAL_K8S_APPARMOR_MODE", raising=False)
    monkeypatch.delenv("LOCAL_K8S_APPARMOR_PROFILE", raising=False)


def test_default_and_custom_profiles_remain_enforced(monkeypatch):
    assert _apparmor_profile_name() == "arca-fuse"
    monkeypatch.setenv("LOCAL_K8S_APPARMOR_PROFILE", "custom-fuse")
    assert _apparmor_profile_name() == "custom-fuse"


@pytest.mark.parametrize("mode", ["", "auto", "unconfined", "disable-typo"])
def test_invalid_modes_fail_before_kubernetes_writes(monkeypatch, mode):
    monkeypatch.setenv("LOCAL_K8S_APPARMOR_MODE", mode)
    plugin = MagicMock()
    with pytest.raises(ValueError, match="LOCAL_K8S_APPARMOR_MODE"):
        LocalK8sArcaSandboxPlugin.create_sync_sandbox(plugin, template_id="test")
    assert plugin.mock_calls == []


def test_empty_profile_is_not_an_opt_out(monkeypatch):
    monkeypatch.setenv("LOCAL_K8S_APPARMOR_PROFILE", " ")
    with pytest.raises(ValueError, match="must not be empty"):
        _apparmor_profile_name()


@pytest.mark.parametrize("mode", ["required", "disabled"])
def test_serialized_bot_policy_and_sidecar_are_consistent(monkeypatch, mode):
    monkeypatch.setenv("LOCAL_K8S_APPARMOR_MODE", mode)
    plugin = MagicMock(CONTAINER_NAME="bot-runtime")
    with patch("kubernetes.client.AppsV1Api") as apps:
        LocalK8sArcaSandboxPlugin._create_deployment(
            plugin, deployment_name="test", template_id="test",
            image="runtime:test", sidecar_image="sidecar:test",
            container_port=8080, envs={}, resource_spec=None,
            configmap_name="headers",
        )
        body = apps.return_value.create_namespaced_deployment.call_args.kwargs["body"]
    containers = ApiClient().sanitize_for_serialization(body)["spec"]["template"]["spec"]["containers"]
    security = containers[0]["securityContext"]
    assert security["privileged"] is True
    if mode == "required":
        assert security["appArmorProfile"] == {"type": "Localhost", "localhostProfile": "arca-fuse"}
    else:
        assert "appArmorProfile" not in security
        assert "Unconfined" not in str(containers)
    assert containers[1]["securityContext"] == {"capabilities": {"add": ["NET_ADMIN"]}}
