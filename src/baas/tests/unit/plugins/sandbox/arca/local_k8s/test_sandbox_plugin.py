"""Tests for local_k8s Arca sandbox plugin."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.device_manage import ArcaCredentials
from secbaas.community.api.device_manage import ResourceSpecification
from secbaas.community.plugins.sandbox.arca.local_k8s import (
    LocalK8sArcaSandbox,
    LocalK8sArcaSandboxPlugin,
)
from secbaas.community.spi.sandbox.arca import ArcaSandboxInfo


def _make_credentials(**overrides: object) -> ArcaCredentials:
    defaults = {
        "template_id": 1,
        "template_uuid": "tpl-test",
        "tenant_name": "test-tenant",
        "base_url": "",
        "api_key": "",
    }
    defaults.update(overrides)
    return ArcaCredentials(**defaults)


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture
def plugin(mock_client):
    # Plugin now reads local_k8s params from env vars.
    with patch.dict(
        os.environ,
        {
            "LOCAL_K8S_NAMESPACE": "default",
            "LOCAL_K8S_IMAGE": "bot-runtime:latest",
            "LOCAL_K8S_CONTAINER_PORT": "8080",
        },
        clear=False,
    ), patch(
        "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
        return_value="apiVersion: v1\nkind: Config",
    ):
        plugin = LocalK8sArcaSandboxPlugin(credentials=_make_credentials())
        plugin._client_manager.get_client = lambda *args, **kwargs: mock_client
        yield plugin




class TestLocalK8sPluginCreate:
    """Tests for create_sync_sandbox."""

    @patch(
        "kubernetes.client.AppsV1Api"
    )
    @patch(
        "kubernetes.client.CoreV1Api"
    )
    def test_create_sync_sandbox_returns_sandbox(
        self,
        mock_core_cls,
        mock_apps_cls,
        plugin,
        mock_client,
    ) -> None:
        """Plugin should create Deployment + Service and return a sandbox."""
        mock_apps = MagicMock()
        mock_apps_cls.return_value = mock_apps
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        mock_pod = MagicMock()
        mock_pod.status.phase = "Running"
        mock_pod.metadata.name = "tpl-test-abc-12345-xyz12"
        mock_core.list_namespaced_pod.return_value.items = [mock_pod]

        sandbox = plugin.create_sync_sandbox(
            template_id="openclaw-default",
            envs={"FOO": "bar"},
            resource_spec=ResourceSpecification(cpu=1, memory=2),
            image="custom-image:tag",
        )

        assert isinstance(sandbox, LocalK8sArcaSandbox)
        # Deployment was created
        mock_apps.create_namespaced_deployment.assert_called_once()
        # Service was created
        mock_core.create_namespaced_service.assert_called_once()
        # Pod was polled
        assert mock_core.list_namespaced_pod.call_count >= 1

        # Verify service type is NodePort
        svc = mock_core.create_namespaced_service.call_args[1]["body"]
        assert svc.spec.type == "NodePort"

    @patch(
        "kubernetes.client.AppsV1Api"
    )
    @patch(
        "kubernetes.client.CoreV1Api"
    )
    def test_create_sync_sandbox_nodeport_uses_service(
        self,
        mock_core_cls,
        mock_apps_cls,
        mock_client,
    ) -> None:
        """NodePort mode should create a NodePort Service."""
        with patch.dict(
            os.environ,
            {
                "LOCAL_K8S_NAMESPACE": "default",
                "LOCAL_K8S_IMAGE": "bot-runtime:latest",
                "LOCAL_K8S_CONTAINER_PORT": "8080",
                "LOCAL_K8S_NODE_PORT": "30080",
            },
            clear=False,
        ), patch(
            "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
            return_value="apiVersion: v1\nkind: Config",
        ):
            plugin = LocalK8sArcaSandboxPlugin(credentials=_make_credentials())
            plugin._client_manager.get_client = lambda *args, **kwargs: mock_client

            mock_apps = MagicMock()
            mock_apps_cls.return_value = mock_apps
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core

            mock_pod = MagicMock()
            mock_pod.status.phase = "Running"
            mock_pod.metadata.name = "bot-pod"
            mock_core.list_namespaced_pod.return_value.items = [mock_pod]

            plugin.create_sync_sandbox(template_id="openclaw-default")

        svc = mock_core.create_namespaced_service.call_args[1]["body"]
        assert svc.spec.type == "NodePort"
        assert svc.spec.ports[0].node_port == 30080

    def test_create_sync_sandbox_missing_image_raises(self, mock_client) -> None:
        """Missing image should raise ValueError."""
        with patch.dict(
            os.environ,
            {
                "LOCAL_K8S_IMAGE": "",
            },
            clear=False,
        ), patch(
            "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
            return_value="apiVersion: v1\nkind: Config",
        ):
            plugin = LocalK8sArcaSandboxPlugin(credentials=_make_credentials())
            plugin._client_manager.get_client = lambda *args, **kwargs: mock_client

            with pytest.raises(ValueError, match="requires an image"):
                plugin.create_sync_sandbox(template_id="openclaw-default")


class TestLocalK8sPluginResolve:
    """Tests for connection info resolution."""

    @patch(
        "kubernetes.client.CoreV1Api"
    )
    def test_resolve_ws_nodeport_reads_assigned_port(
        self,
        mock_core_cls,
        mock_client,
    ) -> None:
        """nodeport mode reads auto-assigned NodePort."""
        with patch.dict(
            os.environ,
            {
                "LOCAL_K8S_NAMESPACE": "default",
                "LOCAL_K8S_IMAGE": "bot-runtime:latest",
                "LOCAL_K8S_CONTAINER_PORT": "8080",
            },
            clear=False,
        ), patch(
            "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
            return_value="apiVersion: v1\nkind: Config",
        ):
            plugin = LocalK8sArcaSandboxPlugin(credentials=_make_credentials())
            plugin._client_manager.get_client = lambda *args, **kwargs: mock_client

            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_svc = MagicMock()
            mock_svc.spec.ports = [MagicMock(node_port=30081)]
            mock_core.read_namespaced_service.return_value = mock_svc

            conn = plugin.resolve_ws_conn_info(
                paas_device_id="tpl-test-abc",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert conn.ws_url == "ws://localhost:30081/api/openclaw/ws"

    @patch(
        "kubernetes.client.CoreV1Api"
    )
    def test_resolve_http_nodeport(
        self,
        mock_core_cls,
        mock_client,
    ) -> None:
        """HTTP connection resolves to localhost via NodePort."""
        with patch.dict(
            os.environ,
            {
                "LOCAL_K8S_NAMESPACE": "default",
                "LOCAL_K8S_IMAGE": "bot-runtime:latest",
                "LOCAL_K8S_CONTAINER_PORT": "8080",
            },
            clear=False,
        ), patch(
            "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
            return_value="apiVersion: v1\nkind: Config",
        ):
            plugin = LocalK8sArcaSandboxPlugin(credentials=_make_credentials())
            plugin._client_manager.get_client = lambda *args, **kwargs: mock_client

            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_svc = MagicMock()
            mock_svc.spec.ports = [MagicMock(node_port=30082)]
            mock_core.read_namespaced_service.return_value = mock_svc

            conn = plugin.resolve_http_connection_info(
                paas_device_id="tpl-test-abc", port=8080, path="/health"
            )

        assert conn.http_url == "http://localhost:30082/health"


class TestLocalK8sSandbox:
    """Tests for LocalK8sArcaSandbox."""

    @patch(
        "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox.LocalK8sArcaSandbox._read_pod"
    )
    def test_get_info_returns_running(self, mock_read_pod, mock_client) -> None:
        mock_pod = MagicMock()
        mock_pod.status.phase = "Running"
        mock_pod.status.pod_ip = "10.42.0.10"
        mock_read_pod.return_value = mock_pod

        sandbox = LocalK8sArcaSandbox(
            sandbox_id="tpl-test-abc",
            pod_name="bot-pod",
            namespace="default",
            template_id="openclaw-default",
            client=mock_client,
            container_name="bot-runtime",
            resources=ResourceSpecification(cpu=1, memory=2),
        )

        info = sandbox.get_info()
        assert isinstance(info, ArcaSandboxInfo)
        assert info.status == "Running"
        assert info.is_ready is True
        assert info.metadata["pod_ip"] == "10.42.0.10"

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
    def test_destroy_deletes_resources(
        self,
        mock_core_cls,
        mock_apps_cls,
        mock_client,
    ) -> None:
        mock_apps = MagicMock()
        mock_apps_cls.return_value = mock_apps
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        sandbox = LocalK8sArcaSandbox(
            sandbox_id="tpl-test-abc",
            pod_name="bot-pod",
            namespace="default",
            template_id="openclaw-default",
            client=mock_client,
            container_name="bot-runtime",
        )

        assert sandbox.destroy() is True
        mock_apps.delete_namespaced_deployment.assert_called_once()
        mock_core.delete_namespaced_service.assert_called_once()

    def test_update_outbound_rule_and_extend_ttl_noop(self, mock_client) -> None:
        sandbox = LocalK8sArcaSandbox(
            sandbox_id="tpl-test-abc",
            pod_name="bot-pod",
            namespace="default",
            template_id="openclaw-default",
            client=mock_client,
            container_name="bot-runtime",
        )

        assert sandbox.update_outbound_rule(MagicMock(), MagicMock()) is True
        assert sandbox.extend_ttl(10) is True
