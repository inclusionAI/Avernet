"""Tests for local_k8s Arca sandbox plugin."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
import yaml

from secbaas.community.api.device_manage import (
    ArcaCredentials,
    HeaderOperationRule,
    OutBoundOperationRule,
    OutBoundOperationRuleUpdatedMode,
    ResourceSpecification,
    Storage,
)
from secbaas.community.plugins.sandbox.arca.local_k8s import (
    LocalK8sArcaSandbox,
    LocalK8sArcaSandboxPlugin,
)
from secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin import (
    LocalK8sClientManager,
    _convert_outbound_rules,
    _image_pull_policy,
    _use_incluster_config,
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


class TestOutboundRulesConversion:
    def test_overlapping_domain_groups_emit_one_rule_per_domain(self) -> None:
        """Overlapping groups must not produce duplicate Envoy domains."""
        rule = OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["a.example.com", "b.example.com"],
                    action="set",
                    header_name="X-A",
                    value="value-a",
                ),
                HeaderOperationRule(
                    domains=["b.example.com", "c.example.com"],
                    action="set",
                    header_name="X-B",
                    value="value-b",
                ),
            ]
        )

        rendered = yaml.safe_load(_convert_outbound_rules(rule))
        assert [item["domains"] for item in rendered["rules"]] == [
            ["a.example.com"],
            ["b.example.com"],
            ["c.example.com"],
        ]
        assert rendered["rules"][1]["set"] == [
            {"header": "X-A", "value": "value-a"},
            {"header": "X-B", "value": "value-b"},
        ]


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture
def plugin(mock_client):
    # Plugin now reads local_k8s params from env vars.
    with (
        patch.dict(
            os.environ,
            {
                "LOCAL_K8S_NAMESPACE": "default",
                "LOCAL_K8S_IMAGE": "bot-runtime:latest",
                "LOCAL_K8S_CONTAINER_PORT": "8080",
                "LOCAL_K8S_OUTBOUND_RULE": "",
            },
            clear=False,
        ),
        patch(
            "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
            return_value="apiVersion: v1\nkind: Config",
        ),
    ):
        plugin = LocalK8sArcaSandboxPlugin(credentials=_make_credentials())
        plugin._client_manager.get_client = lambda *args, **kwargs: mock_client
        yield plugin


class TestLocalK8sPluginCreate:
    """Tests for create_sync_sandbox."""

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
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
        # ConfigMap was created
        mock_core.create_namespaced_config_map.assert_called_once()
        # Deployment was created
        mock_apps.create_namespaced_deployment.assert_called_once()
        # Service was created
        mock_core.create_namespaced_service.assert_called_once()
        # Pod was polled
        assert mock_core.list_namespaced_pod.call_count >= 1

        # Verify service type is NodePort
        svc = mock_core.create_namespaced_service.call_args[1]["body"]
        assert svc.spec.type == "NodePort"

        # Deployment has bot-runtime and envoy-sidecar
        deployment = mock_apps.create_namespaced_deployment.call_args[1]["body"]
        container_names = [c.name for c in deployment.spec.template.spec.containers]
        assert container_names == ["bot-runtime", "envoy-sidecar"]
        sidecar = deployment.spec.template.spec.containers[1]
        assert sidecar.env[0].name == "HEADER_RULES"
        assert sidecar.env[0].value == "/etc/sidecar/header-rules/header-rules.yaml"
        assert sidecar.volume_mounts[0].mount_path == "/etc/sidecar/header-rules"
        assert not sidecar.volume_mounts[0].sub_path

        bot_container = deployment.spec.template.spec.containers[0]
        assert bot_container.security_context.privileged is True
        assert bot_container.security_context.app_armor_profile.type == "Localhost"
        assert (
            bot_container.security_context.app_armor_profile.localhost_profile
            == "arca-fuse"
        )
        assert bot_container.volume_mounts[0].name == "fuse-device"
        assert bot_container.volume_mounts[0].mount_path == "/opt/dev/fuse"

        volumes = deployment.spec.template.spec.volumes
        assert len(volumes) == 2
        assert volumes[0].config_map.name.startswith("envoy-header-rules-")
        assert volumes[1].host_path.path == "/dev/fuse"
        assert volumes[1].host_path.type == "CharDevice"

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
    def test_create_sync_sandbox_nodeport_uses_service(
        self,
        mock_core_cls,
        mock_apps_cls,
        mock_client,
    ) -> None:
        """NodePort mode should create a NodePort Service."""
        with (
            patch.dict(
                os.environ,
                {
                    "LOCAL_K8S_NAMESPACE": "default",
                    "LOCAL_K8S_IMAGE": "bot-runtime:latest",
                    "LOCAL_K8S_CONTAINER_PORT": "8080",
                    "LOCAL_K8S_NODE_PORT": "30080",
                },
                clear=False,
            ),
            patch(
                "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
                return_value="apiVersion: v1\nkind: Config",
            ),
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

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
    def test_create_sync_sandbox_uses_outbound_rule_from_env(
        self,
        mock_core_cls,
        mock_apps_cls,
        plugin,
        mock_client,
    ) -> None:
        """LOCAL_K8S_OUTBOUND_RULE 应作为入参 fallback 写入 ConfigMap。"""
        rule = OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["example.com"],
                    action="set",
                    header_name="X-Token",
                    value="secret",
                )
            ]
        )
        with patch.dict(
            os.environ,
            {"LOCAL_K8S_OUTBOUND_RULE": rule.model_dump_json()},
            clear=False,
        ):
            mock_apps = MagicMock()
            mock_apps_cls.return_value = mock_apps
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core

            mock_pod = MagicMock()
            mock_pod.status.phase = "Running"
            mock_pod.metadata.name = "bot-pod"
            mock_core.list_namespaced_pod.return_value.items = [mock_pod]

            plugin.create_sync_sandbox(template_id="openclaw-default")

        configmap = mock_core.create_namespaced_config_map.call_args[1]["body"]
        yaml_text = configmap.data["header-rules.yaml"]
        assert "example.com" in yaml_text
        assert "X-Token" in yaml_text
        assert "secret" in yaml_text

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
    def test_create_sync_sandbox_outbound_rule_arg_overrides_env(
        self,
        mock_core_cls,
        mock_apps_cls,
        plugin,
        mock_client,
    ) -> None:
        """入参 outbound_operation_rule 优先级高于 LOCAL_K8S_OUTBOUND_RULE。"""
        env_rule = OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["env.example.com"],
                    action="set",
                    header_name="X-Env",
                    value="env",
                )
            ]
        )
        arg_rule = OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["arg.example.com"],
                    action="set",
                    header_name="X-Arg",
                    value="arg",
                )
            ]
        )
        with patch.dict(
            os.environ,
            {"LOCAL_K8S_OUTBOUND_RULE": env_rule.model_dump_json()},
            clear=False,
        ):
            mock_apps = MagicMock()
            mock_apps_cls.return_value = mock_apps
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core

            mock_pod = MagicMock()
            mock_pod.status.phase = "Running"
            mock_pod.metadata.name = "bot-pod"
            mock_core.list_namespaced_pod.return_value.items = [mock_pod]

            plugin.create_sync_sandbox(
                template_id="openclaw-default",
                outbound_operation_rule=arg_rule,
            )

        configmap = mock_core.create_namespaced_config_map.call_args[1]["body"]
        yaml_text = configmap.data["header-rules.yaml"]
        assert "arg.example.com" in yaml_text
        assert "X-Arg" in yaml_text
        assert "env.example.com" not in yaml_text

    def test_connect_sync_sandbox_preserves_sidecar_container(
        self,
        plugin,
        mock_client,
    ) -> None:
        """重连时也要保留 sidecar 容器名，供原地刷新使用。"""
        with patch.object(
            LocalK8sArcaSandboxPlugin,
            "_find_pod_name",
            return_value="bot-pod",
        ):
            sandbox = plugin.connect_sync_sandbox("tpl-test-abc")
        assert sandbox._sidecar_container_name == "envoy-sidecar"

    def test_create_sync_sandbox_missing_image_raises(self, mock_client) -> None:
        """Missing image should raise ValueError."""
        with (
            patch.dict(
                os.environ,
                {
                    "LOCAL_K8S_IMAGE": "",
                },
                clear=False,
            ),
            patch(
                "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
                return_value="apiVersion: v1\nkind: Config",
            ),
        ):
            plugin = LocalK8sArcaSandboxPlugin(credentials=_make_credentials())
            plugin._client_manager.get_client = lambda *args, **kwargs: mock_client

            with pytest.raises(ValueError, match="requires an image"):
                plugin.create_sync_sandbox(template_id="openclaw-default")

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
    def test_create_sync_sandbox_with_bot_id_creates_shared_pvc_and_subpath(
        self,
        mock_core_cls,
        mock_apps_cls,
        plugin,
        mock_client,
    ) -> None:
        """携带 bot_id 时，应创建共享 PVC 并以 storage_id 为 subPath 挂载到 /home/admin。"""
        mock_apps = MagicMock()
        mock_apps_cls.return_value = mock_apps
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        mock_pod = MagicMock()
        mock_pod.status.phase = "Running"
        mock_pod.metadata.name = "tpl-test-abc-12345-xyz12"
        mock_core.list_namespaced_pod.return_value.items = [mock_pod]

        # 模拟 PVC 不存在，触发创建。
        from kubernetes.client.rest import ApiException

        mock_core.read_namespaced_persistent_volume_claim.side_effect = ApiException(
            status=404
        )

        bot_id = "test-bot-123"
        expected_pvc_name = "bot-shared-data"
        sandbox = plugin.create_sync_sandbox(
            template_id="openclaw-default",
            metadata={"bot_id": bot_id},
        )

        # 共享 PVC 被创建
        mock_core.create_namespaced_persistent_volume_claim.assert_called_once()
        pvc = mock_core.create_namespaced_persistent_volume_claim.call_args[1]["body"]
        assert pvc.metadata.name == expected_pvc_name
        assert pvc.spec.access_modes == ["ReadWriteOnce"]
        assert pvc.spec.resources.requests["storage"] == "10Gi"

        # Deployment 挂载了共享 PVC 和 fuse 设备
        deployment = mock_apps.create_namespaced_deployment.call_args[1]["body"]
        volumes = deployment.spec.template.spec.volumes
        assert len(volumes) == 3
        assert volumes[0].name == "header-rules"
        assert volumes[1].name == "bot-data"
        assert volumes[1].persistent_volume_claim.claim_name == expected_pvc_name
        assert volumes[2].name == "fuse-device"
        assert volumes[2].host_path.path == "/dev/fuse"
        assert volumes[2].host_path.type == "CharDevice"

        # bot-runtime 容器以 storage_id 为 subPath 挂载到 /home/admin
        bot_container = deployment.spec.template.spec.containers[0]
        assert bot_container.security_context.privileged is True
        assert bot_container.security_context.app_armor_profile.type == "Localhost"
        assert (
            bot_container.security_context.app_armor_profile.localhost_profile
            == "arca-fuse"
        )
        assert bot_container.volume_mounts[0].name == "fuse-device"
        assert bot_container.volume_mounts[0].mount_path == "/opt/dev/fuse"
        assert bot_container.volume_mounts[1].name == "bot-data"
        assert bot_container.volume_mounts[1].mount_path == "/home/admin"
        assert bot_container.volume_mounts[1].sub_path == bot_id

        # Deployment 和 Pod 模板带有 storage-id 注解
        expected_annotations = {"secbaas.local-k8s.io/storage-id": bot_id}
        assert deployment.metadata.annotations == expected_annotations
        assert deployment.spec.template.metadata.annotations == expected_annotations

        # sandbox 携带 storage，供 destroy 时清理
        info = sandbox.get_info()
        assert info.storage is not None
        assert info.storage.storage_id == bot_id
        assert info.storage.type == "pvc"
        assert info.storage.path == "/home/admin"

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
    def test_create_sync_sandbox_prefers_storage_param(
        self,
        mock_core_cls,
        mock_apps_cls,
        plugin,
        mock_client,
    ) -> None:
        """storage 参数优先级高于 metadata.bot_id，与 backend composer 对齐。"""
        mock_apps = MagicMock()
        mock_apps_cls.return_value = mock_apps
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        mock_pod = MagicMock()
        mock_pod.status.phase = "Running"
        mock_pod.metadata.name = "bot-pod"
        mock_core.list_namespaced_pod.return_value.items = [mock_pod]

        # 模拟 PVC 不存在，触发创建。
        from kubernetes.client.rest import ApiException

        mock_core.read_namespaced_persistent_volume_claim.side_effect = ApiException(
            status=404
        )

        storage_id = "backend-bot-456"
        sandbox = plugin.create_sync_sandbox(
            template_id="openclaw-default",
            metadata={"bot_id": "metadata-bot-123"},
            storage=Storage(
                storage_id=storage_id,
                type="nas",
                path="/home/admin",
                quota="2Gi",
            ),
        )

        # 共享 PVC 名称与 storage_id 无关；subPath 使用 storage_id。
        pvc = mock_core.create_namespaced_persistent_volume_claim.call_args[1]["body"]
        assert pvc.metadata.name == "bot-shared-data"
        assert pvc.spec.resources.requests["storage"] == "10Gi"

        deployment = mock_apps.create_namespaced_deployment.call_args[1]["body"]
        bot_container = deployment.spec.template.spec.containers[0]
        assert bot_container.volume_mounts[0].name == "fuse-device"
        assert bot_container.volume_mounts[1].sub_path == storage_id
        assert deployment.metadata.annotations == {
            "secbaas.local-k8s.io/storage-id": storage_id
        }
        info = sandbox.get_info()
        assert info.storage.storage_id == storage_id
        assert info.storage.quota == "2Gi"

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
    def test_create_sync_sandbox_without_bot_id_skips_pvc(
        self,
        mock_core_cls,
        mock_apps_cls,
        plugin,
        mock_client,
    ) -> None:
        """未提供 bot_id 时，不创建 PVC，保持原有行为。"""
        mock_apps = MagicMock()
        mock_apps_cls.return_value = mock_apps
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        mock_pod = MagicMock()
        mock_pod.status.phase = "Running"
        mock_pod.metadata.name = "bot-pod"
        mock_core.list_namespaced_pod.return_value.items = [mock_pod]

        sandbox = plugin.create_sync_sandbox(template_id="openclaw-default")

        mock_core.create_namespaced_persistent_volume_claim.assert_not_called()
        deployment = mock_apps.create_namespaced_deployment.call_args[1]["body"]
        volumes = deployment.spec.template.spec.volumes
        assert len(volumes) == 2
        assert volumes[0].name == "header-rules"
        assert volumes[1].name == "fuse-device"
        assert deployment.metadata.annotations is None
        assert sandbox.get_info().storage is None

    @patch.dict(
        os.environ, {"LOCAL_K8S_SHARED_PVC_HOST_MOUNT_PATH": "/tmp/bot-shared-data"}
    )
    def test_delete_storage_removes_subpath(self, plugin, mock_client) -> None:
        """delete_storage 应删除共享 PVC 中该 storage_id 对应的 subPath 目录。"""
        import tempfile
        from pathlib import Path

        bot_id = "cleanup-bot"
        host_path = "/tmp/bot-shared-data"
        subpath = Path(host_path) / bot_id

        # 使用临时目录模拟 host mount，避免污染真实路径。
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"LOCAL_K8S_SHARED_PVC_HOST_MOUNT_PATH": tmpdir},
                clear=False,
            ):
                subpath = Path(tmpdir) / bot_id
                subpath.mkdir()
                (subpath / "file.txt").write_text("data")

                result = plugin.delete_storage(bot_id, "test-tenant")

            assert result is True
            assert not subpath.exists()

    def test_delete_storage_no_host_mount_is_noop(self, plugin, mock_client) -> None:
        """未配置共享 PVC host mount 路径时 delete_storage 应为幂等 no-op。"""
        with patch.dict(
            os.environ,
            {"LOCAL_K8S_SHARED_PVC_HOST_MOUNT_PATH": ""},
            clear=False,
        ):
            result = plugin.delete_storage("missing-bot", "test-tenant")
            assert result is True

    @patch("kubernetes.client.AppsV1Api")
    def test_connect_sync_sandbox_restores_storage_id_from_annotation(
        self,
        mock_apps_cls,
        plugin,
        mock_client,
    ) -> None:
        """重连时应从 Deployment 注解恢复 storage id 并重建 storage。"""
        bot_id = "reconnect-bot"
        deployment_name = "tpl-test-abc"

        mock_deployment = MagicMock()
        mock_deployment.metadata.annotations = {
            "secbaas.local-k8s.io/storage-id": bot_id,
        }
        mock_apps = MagicMock()
        mock_apps.read_namespaced_deployment.return_value = mock_deployment
        mock_apps_cls.return_value = mock_apps

        with patch.object(
            LocalK8sArcaSandboxPlugin,
            "_find_pod_name",
            return_value="bot-pod",
        ):
            sandbox = plugin.connect_sync_sandbox(deployment_name)

        mock_apps.read_namespaced_deployment.assert_called_once_with(
            name=deployment_name, namespace="default"
        )
        info = sandbox.get_info()
        assert info.storage is not None
        assert info.storage.storage_id == bot_id

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
    def test_shared_pvc_is_created_idempotently(
        self,
        mock_core_cls,
        mock_apps_cls,
        plugin,
        mock_client,
    ) -> None:
        """共享 PVC 已存在时不再重复创建。"""
        from kubernetes.client.rest import ApiException

        mock_apps = MagicMock()
        mock_apps_cls.return_value = mock_apps
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        mock_core.read_namespaced_persistent_volume_claim.side_effect = ApiException(
            status=404
        )

        mock_pod = MagicMock()
        mock_pod.status.phase = "Running"
        mock_pod.metadata.name = "bot-pod"
        mock_core.list_namespaced_pod.return_value.items = [mock_pod]

        plugin.create_sync_sandbox(
            template_id="openclaw-default",
            metadata={"bot_id": "bot-1"},
        )

        # 第一次调用创建 PVC
        mock_core.create_namespaced_persistent_volume_claim.assert_called_once()

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
    def test_shared_pvc_reuses_existing(
        self,
        mock_core_cls,
        mock_apps_cls,
        plugin,
        mock_client,
    ) -> None:
        """共享 PVC 已存在时直接复用。"""
        mock_apps = MagicMock()
        mock_apps_cls.return_value = mock_apps
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        mock_core.read_namespaced_persistent_volume_claim.return_value = MagicMock()

        mock_pod = MagicMock()
        mock_pod.status.phase = "Running"
        mock_pod.metadata.name = "bot-pod"
        mock_core.list_namespaced_pod.return_value.items = [mock_pod]

        plugin.create_sync_sandbox(
            template_id="openclaw-default",
            metadata={"bot_id": "bot-1"},
        )

        # 已存在则不创建
        mock_core.create_namespaced_persistent_volume_claim.assert_not_called()

        # Deployment 仍使用共享 PVC
        deployment = mock_apps.create_namespaced_deployment.call_args[1]["body"]
        volumes = deployment.spec.template.spec.volumes
        assert volumes[1].persistent_volume_claim.claim_name == "bot-shared-data"

    def test_shared_pvc_name_is_env_overridable(self) -> None:
        """共享 PVC 名称可通过环境变量覆盖。"""
        from secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin import (
            _shared_pvc_name,
        )

        with patch.dict(
            os.environ, {"LOCAL_K8S_SHARED_PVC_NAME": "my-shared-data"}, clear=False
        ):
            assert _shared_pvc_name() == "my-shared-data"

        # 默认值
        with patch.dict(os.environ, {"LOCAL_K8S_SHARED_PVC_NAME": ""}, clear=False):
            assert _shared_pvc_name() == "bot-shared-data"


class TestLocalK8sPluginResolve:
    """Tests for connection info resolution."""

    @patch("kubernetes.client.CoreV1Api")
    def test_resolve_ws_nodeport_reads_assigned_port(
        self,
        mock_core_cls,
        mock_client,
    ) -> None:
        """nodeport mode reads auto-assigned NodePort."""
        with (
            patch.dict(
                os.environ,
                {
                    "LOCAL_K8S_NAMESPACE": "default",
                    "LOCAL_K8S_IMAGE": "bot-runtime:latest",
                    "LOCAL_K8S_CONTAINER_PORT": "8080",
                },
                clear=False,
            ),
            patch(
                "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
                return_value="apiVersion: v1\nkind: Config",
            ),
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

    @patch("kubernetes.client.CoreV1Api")
    def test_resolve_http_nodeport(
        self,
        mock_core_cls,
        mock_client,
    ) -> None:
        """HTTP connection resolves to localhost via NodePort."""
        with (
            patch.dict(
                os.environ,
                {
                    "LOCAL_K8S_NAMESPACE": "default",
                    "LOCAL_K8S_IMAGE": "bot-runtime:latest",
                    "LOCAL_K8S_CONTAINER_PORT": "8080",
                },
                clear=False,
            ),
            patch(
                "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
                return_value="apiVersion: v1\nkind: Config",
            ),
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

    @patch("kubernetes.client.CoreV1Api")
    def test_resolve_http_incluster_uses_cluster_ip_and_service_port(
        self,
        mock_core_cls,
        mock_client,
    ) -> None:
        """In-cluster backend calls must not use localhost/NodePort.

        The backend pod cannot reach a bot runtime through the host's
        localhost NodePort, so the connection info must use the runtime
        Service's ClusterIP and port. Otherwise OpenAPI engine-backed routes
        surface the transport failure as a generic 502.
        """
        with (
            patch.dict(
                os.environ,
                {
                    "LOCAL_K8S_KUBECONFIG": "in-cluster",
                    "LOCAL_K8S_NAMESPACE": "default",
                    "LOCAL_K8S_IMAGE": "bot-runtime:latest",
                    "LOCAL_K8S_CONTAINER_PORT": "8080",
                },
                clear=False,
            ),
            patch(
                "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
                return_value="apiVersion: v1\nkind: Config",
            ),
            patch(
                "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._use_incluster_config",
                return_value=True,
            ),
        ):
            plugin = LocalK8sArcaSandboxPlugin(credentials=_make_credentials())
            plugin._client_manager.get_client = lambda *args, **kwargs: mock_client

            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_svc = MagicMock()
            mock_svc.spec.cluster_ip = "10.96.42.7"
            mock_svc.spec.ports = [MagicMock(port=8080, node_port=30082)]
            mock_core.read_namespaced_service.return_value = mock_svc

            conn = plugin.resolve_http_connection_info(
                paas_device_id="tpl-test-abc", port=8080, path="/health"
            )

        assert conn.http_url == "http://10.96.42.7:8080/health"
        assert conn.target == "10.96.42.7:8080"


class TestLocalK8sPluginA2aBaseUrl:
    """The A2A face address handed out beside the ws URL.

    Empty string is the additive default: a deployment that configured no
    template, a Service with no NodePort, or a discovery failure must degrade
    to "no A2A address" — never break the chat connection, never guess a host.
    """

    _INCLUSTER_ENV = {
        "LOCAL_K8S_KUBECONFIG": "in-cluster",
        "LOCAL_K8S_NAMESPACE": "default",
        "LOCAL_K8S_IMAGE": "bot-runtime:latest",
        "LOCAL_K8S_CONTAINER_PORT": "8080",
    }

    @patch("kubernetes.client.CoreV1Api")
    def test_nodeport_mode_derives_a2a_from_the_same_public_port(
        self, mock_core_cls, mock_client
    ) -> None:
        """Out-of-cluster the ws URL already addresses the host machine, so
        the A2A face sits on the identical host:port over plain HTTP —
        no template required."""
        with (
            patch.dict(
                os.environ,
                {
                    "LOCAL_K8S_NAMESPACE": "default",
                    "LOCAL_K8S_IMAGE": "bot-runtime:latest",
                    "LOCAL_K8S_CONTAINER_PORT": "8080",
                },
                clear=False,
            ),
            patch(
                "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._use_incluster_config",
                return_value=False,
            ),
            patch(
                "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
                return_value="apiVersion: v1\nkind: Config",
            ),
        ):
            plugin = LocalK8sArcaSandboxPlugin(credentials=_make_credentials())
            plugin._client_manager.get_client = lambda *a, **kw: mock_client
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
        assert conn.a2a_base_url == "http://localhost:30081"

    def _incluster_plugin(self, mock_client, mock_core_cls=None) -> LocalK8sArcaSandboxPlugin:
        plugin = LocalK8sArcaSandboxPlugin(
            credentials=_make_credentials(), arca_utils=MagicMock()
        )
        plugin._client_manager.get_client = lambda *a, **kw: mock_client
        return plugin

    @patch("kubernetes.client.CoreV1Api")
    def test_incluster_without_template_publishes_no_a2a_address(
        self, mock_core_cls, mock_client
    ) -> None:
        """In-cluster the template IS the deployment's host convention; its
        absence means BaaS cannot promise any browser-reachable host, and an
        empty string — not a guess — is the honest answer."""
        with patch.dict(
            os.environ,
            {**self._INCLUSTER_ENV, "LOCAL_K8S_A2A_URL_TEMPLATE": ""},
            clear=True,
        ):
            plugin = self._incluster_plugin(mock_client, mock_core_cls)
            conn = plugin.resolve_ws_conn_info(
                paas_device_id="tpl-test-abc",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert conn.a2a_base_url == ""
        mock_core_cls.assert_not_called()

    @patch("kubernetes.client.CoreV1Api")
    def test_incluster_template_substitutes_discovered_nodeport(
        self, mock_core_cls, mock_client
    ) -> None:
        """The NodePort comes from the kube api — the one place that knows
        it — and the host part from the configured template, placeholders
        filled with the real Service/namespace facts."""
        with patch.dict(
            os.environ,
            {
                **self._INCLUSTER_ENV,
                "LOCAL_K8S_A2A_URL_TEMPLATE": (
                    "http://{service_name}.stable.alipay.net:{node_port}"
                ),
            },
            clear=True,
        ):
            plugin = self._incluster_plugin(mock_client, mock_core_cls)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_svc = MagicMock()
            mock_svc.spec.ports = [MagicMock(node_port=30082)]
            mock_core.read_namespaced_service.return_value = mock_svc

            # 新契约（review P2 终闭环）：签发零 kube 查询——首笔未就绪
            # 先空地址，后台发现 join 后的下一笔带上实查 NodePort
            first = plugin.resolve_ws_conn_info(
                paas_device_id="tpl-test-abc",
                port=8080,
                path="/api/openclaw/ws",
            )
            plugin.a2a_discovery_join("tpl-test-abc")
            conn = plugin.resolve_ws_conn_info(
                paas_device_id="tpl-test-abc",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert first.a2a_base_url == ""
        assert conn.a2a_base_url == (
            "http://tpl-test-abc-svc.stable.alipay.net:30082"
        )

    @patch("kubernetes.client.CoreV1Api")
    def test_create_service_response_warms_a2a_cache(
        self, mock_core_cls, mock_client
    ) -> None:
        """create 响应已完成 NodePort 分配——直接预热，后续签发零 kube
        查询（review P2：可选地址发现彻底离开签发路径，含首轮签发）。"""
        with patch.dict(
            os.environ,
            {
                **self._INCLUSTER_ENV,
                "LOCAL_K8S_A2A_URL_TEMPLATE": (
                    "http://{service_name}.stable.alipay.net:{node_port}"
                ),
            },
            clear=True,
        ):
            plugin = self._incluster_plugin(mock_client, mock_core_cls)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            created = MagicMock()
            created.spec.ports = [MagicMock(node_port=30090)]
            mock_core.create_namespaced_service.return_value = created

            plugin._create_service(
                deployment_name="warm-abc", container_port=20003
            )

            conn = plugin.resolve_ws_conn_info(
                paas_device_id="warm-abc",
                port=20003, path="/api/openclaw/ws",
            )
            assert conn.a2a_base_url == (
                "http://warm-abc-svc.stable.alipay.net:30090"
            )
            # 预热命中：签发行程未做任何 read 查询、未开后台线程
            mock_core.read_namespaced_service.assert_not_called()

    @patch("kubernetes.client.CoreV1Api")
    def test_slow_k8s_does_not_delay_ws_issuance(
        self, mock_core_cls, mock_client
    ) -> None:
        """review P2（终闭环）：kube 慢不仅是事件循环问题——旧实现会让
        *本次* WS 签发同步等完发现。现契约：签发零 kube 查询，缓存未就绪
        立即返回空 A2A 地址 + 后台发现，后续签发命中。"""
        import threading
        import time as _time

        with patch.dict(
            os.environ,
            {
                **self._INCLUSTER_ENV,
                "LOCAL_K8S_A2A_URL_TEMPLATE": (
                    "http://{service_name}.stable.alipay.net:{node_port}"
                ),
            },
            clear=True,
        ):
            plugin = self._incluster_plugin(mock_client, mock_core_cls)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_svc = MagicMock()
            mock_svc.spec.ports = [MagicMock(node_port=30083)]
            mock_core.read_namespaced_service = MagicMock(
                side_effect=lambda *a, **kw: (_time.sleep(0.3), mock_svc)[1]
            )

            start = _time.monotonic()
            conn = plugin.resolve_ws_conn_info(
                paas_device_id="slow-k8s-abc",
                port=8080, path="/api/openclaw/ws",
            )
            elapsed = _time.monotonic() - start

            assert conn.a2a_base_url == ""      # 缓存未就绪：空地址立即返回
            assert elapsed < 0.1, (
                f"ws issuance waited {elapsed:.2f}s for slow k8s — "
                "discovery must not be on the critical path")
            # 后台发现完成、写入缓存后，下一笔签发命中
            plugin.a2a_discovery_join("slow-k8s-abc")
            conn2 = plugin.resolve_ws_conn_info(
                paas_device_id="slow-k8s-abc",
                port=8080, path="/api/openclaw/ws",
            )
            assert conn2.a2a_base_url == (
                "http://slow-k8s-abc-svc.stable.alipay.net:30083"
            )

    @patch("kubernetes.client.CoreV1Api")
    def test_a2a_discovery_failure_negative_caches(
        self, mock_core_cls, mock_client
    ) -> None:
        """review P2：持续故障不得反复等待。失败写「负缓存+短 TTL」，TTL 内
        未就绪的签发一律空地址、零新查询零新线程。"""
        import time as _time

        with patch.dict(
            os.environ,
            {
                **self._INCLUSTER_ENV,
                "LOCAL_K8S_A2A_URL_TEMPLATE": (
                    "http://{service_name}.stable.alipay.net:{node_port}"
                ),
            },
            clear=True,
        ):
            plugin = self._incluster_plugin(mock_client, mock_core_cls)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_core.read_namespaced_service = MagicMock(
                side_effect=RuntimeError("api down")
            )

            for _ in range(3):
                conn = plugin.resolve_ws_conn_info(
                    paas_device_id="neg-cache-abc",
                    port=8080, path="/api/openclaw/ws",
                )
                assert conn.a2a_base_url == ""
                plugin.a2a_discovery_join("neg-cache-abc")

            # 一次失败探查 + TTL 内不重查（3 次签发共 1 次 kube 读）
            assert mock_core.read_namespaced_service.call_count == 1

    @patch("kubernetes.client.CoreV1Api")
    def test_a2a_discovery_thread_start_failure_degrades(
        self, mock_core_cls, mock_client
    ) -> None:
        """review P2-1（调度失败边界）：后台发现线程启动失败（线程资源不足）必须
        就地降级——WS 签发照常返回（a2a 面空串，零异常上抛）、零 kube 查询、
        in-flight/线程记录零残留（残留会永久旁路该设备的后续调度）、负缓存
        就位；TTL 出清后重新调度即恢复。"""
        import time as _time

        with patch.dict(
            os.environ,
            {
                **self._INCLUSTER_ENV,
                "LOCAL_K8S_A2A_URL_TEMPLATE": (
                    "http://{service_name}.stable.alipay.net:{node_port}"
                ),
            },
            clear=True,
        ):
            plugin = self._incluster_plugin(mock_client, mock_core_cls)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_svc = MagicMock()
            mock_svc.spec.ports = [MagicMock(node_port=30095)]
            mock_core.read_namespaced_service.return_value = mock_svc
            plugin._arca_utils.build_proxypass_url.return_value = (
                "ws://proxypass/api/openclaw/ws"
            )

            class _ExplodingThread:
                def __init__(self, *args, **kwargs): ...

                def start(self):
                    raise RuntimeError("can't start new thread")

                def is_alive(self):
                    return False

            with patch(
                "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin.threading.Thread",
                _ExplodingThread,
            ):
                conn = plugin.resolve_ws_conn_info(
                    paas_device_id="boom-abc",
                    port=8080, path="/api/openclaw/ws",
                )

            # WS 签发不被调度异常打断：正常返回且 a2a 面降级空串，零 kube 查询
            assert conn.ws_url == "ws://proxypass/api/openclaw/ws"
            assert conn.a2a_base_url == ""
            mock_core.read_namespaced_service.assert_not_called()
            # 调度失败清理义务：in-flight / 线程记录零残留 + 负缓存就位
            key = ("default", "boom-abc-svc")
            assert plugin._a2a_inflight == set()
            assert plugin._a2a_threads == {}
            entry = plugin._a2a_node_port_cache[key]
            assert isinstance(entry, tuple) and entry[0] == "error"
            assert entry[1] > _time.monotonic()

            # 负缓存出清（模拟 TTL 过期）后重新调度即恢复：真线程跑完发现
            plugin._a2a_node_port_cache.clear()
            plugin.resolve_ws_conn_info(
                paas_device_id="boom-abc",
                port=8080, path="/api/openclaw/ws",
            )
            plugin.a2a_discovery_join("boom-abc")
            recovered = plugin.resolve_ws_conn_info(
                paas_device_id="boom-abc",
                port=8080, path="/api/openclaw/ws",
            )
            assert recovered.a2a_base_url == (
                "http://boom-abc-svc.stable.alipay.net:30095"
            )
            assert mock_core.read_namespaced_service.call_count == 1

    @patch("kubernetes.client.CoreV1Api")
    def test_a2a_discovery_backpressure_cap_degrades_immediately(
        self, mock_core_cls, mock_client
    ) -> None:
        """review P2-1：后台发现进程级并发上限——容量耗尽立即降级为空地址 +
        短负缓存（零等待、零新线程、零 kube 查询），WS 签发不受影响；
        本 key 不进 in-flight，容量释放后可再调度。"""
        import time as _time

        from secbaas.community.plugins.sandbox.arca.local_k8s._a2a_discovery import (
            _A2A_URL_DISCOVERY_MAX_CONCURRENCY,
        )

        with patch.dict(
            os.environ,
            {
                **self._INCLUSTER_ENV,
                "LOCAL_K8S_A2A_URL_TEMPLATE": (
                    "http://{service_name}.stable.alipay.net:{node_port}"
                ),
            },
            clear=True,
        ):
            plugin = self._incluster_plugin(mock_client, mock_core_cls)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            # 占满并发额度（合成的他设备 key）——触达容量上限
            for i in range(_A2A_URL_DISCOVERY_MAX_CONCURRENCY):
                plugin._a2a_inflight.add(("default", f"busy-{i}-svc"))

            start = _time.monotonic()
            conn = plugin.resolve_ws_conn_info(
                paas_device_id="cap-abc",
                port=8080, path="/api/openclaw/ws",
            )
            elapsed = _time.monotonic() - start

            assert conn.ws_url is not None
            assert conn.a2a_base_url == ""
            assert elapsed < 0.1, (
                f"ws issuance waited {elapsed:.2f}s at capacity — "
                "backpressure must degrade immediately, never queue")
            mock_core.read_namespaced_service.assert_not_called()
            # 零等待策略下既不占额度也不开线程：负缓存承载"稍后再试"
            key = ("default", "cap-abc-svc")
            assert key not in plugin._a2a_inflight
            assert plugin._a2a_threads == {}
            entry = plugin._a2a_node_port_cache[key]
            assert isinstance(entry, tuple) and entry[0] == "error"
            assert entry[1] > _time.monotonic()

            # 容量释放 + 负缓存出清后可再调度（恢复路径不被容量事件旁路）
            plugin._a2a_inflight.clear()
            plugin._a2a_node_port_cache.clear()
            mock_svc = MagicMock()
            mock_svc.spec.ports = [MagicMock(node_port=30096)]
            mock_core.read_namespaced_service.return_value = mock_svc
            plugin.resolve_ws_conn_info(
                paas_device_id="cap-abc",
                port=8080, path="/api/openclaw/ws",
            )
            plugin.a2a_discovery_join("cap-abc")
            recovered = plugin.resolve_ws_conn_info(
                paas_device_id="cap-abc",
                port=8080, path="/api/openclaw/ws",
            )
            assert recovered.a2a_base_url == (
                "http://cap-abc-svc.stable.alipay.net:30096"
            )

    @patch("kubernetes.client.CoreV1Api")
    def test_a2a_discovery_bounded_timeout(self, mock_core_cls, mock_client) -> None:
        """review P2：kube 实查必须在有界超时内返回等待——无 _request_timeout
        的挂起会拖住整条 WS 签发（to_thread 之外的第二道防线）。"""
        with patch.dict(
            os.environ,
            {
                **self._INCLUSTER_ENV,
                "LOCAL_K8S_A2A_URL_TEMPLATE": (
                    "http://{service_name}.stable.alipay.net:{node_port}"
                ),
            },
            clear=True,
        ):
            plugin = self._incluster_plugin(mock_client, mock_core_cls)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_svc = MagicMock()
            mock_svc.spec.ports = [MagicMock(node_port=30082)]
            mock_core.read_namespaced_service.return_value = mock_svc

            plugin.resolve_ws_conn_info(
                paas_device_id="a2a-to-abc",
                port=8080, path="/api/openclaw/ws",
            )

        kwargs = mock_core.read_namespaced_service.call_args.kwargs
        assert kwargs.get("_request_timeout") is not None and kwargs["_request_timeout"] <= 5

    @patch("kubernetes.client.CoreV1Api")
    def test_a2a_discovery_caches_nodeport(
        self, mock_core_cls, mock_client
    ) -> None:
        """review P2：NodePort 对 Service 生命周期内不变——按 (namespace,
        service) 记忆化，重复签发不再打 kube API。"""
        with patch.dict(
            os.environ,
            {
                **self._INCLUSTER_ENV,
                "LOCAL_K8S_A2A_URL_TEMPLATE": (
                    "http://{service_name}.stable.alipay.net:{node_port}"
                ),
            },
            clear=True,
        ):
            plugin = self._incluster_plugin(mock_client, mock_core_cls)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_svc = MagicMock()
            mock_svc.spec.ports = [MagicMock(node_port=30082)]
            mock_core.read_namespaced_service.return_value = mock_svc

            first = plugin.resolve_ws_conn_info(
                paas_device_id="a2a-cache-abc",
                port=8080, path="/api/openclaw/ws",
            )
            assert first.a2a_base_url == ""       # 未就绪：先空地址
            plugin.a2a_discovery_join("a2a-cache-abc")
            for _ in range(2):
                conn = plugin.resolve_ws_conn_info(
                    paas_device_id="a2a-cache-abc",
                    port=8080, path="/api/openclaw/ws",
                )
                assert conn.a2a_base_url == (
                    "http://a2a-cache-abc-svc.stable.alipay.net:30082"
                )

        assert mock_core.read_namespaced_service.call_count == 1

    @patch("kubernetes.client.CoreV1Api")
    def test_incluster_missing_nodeport_degrades_to_empty(
        self, mock_core_cls, mock_client
    ) -> None:
        """A ClusterIP-shaped Service under a configured template is a
        describable configuration state, answered with the empty string rather
        than an exception that takes the chat connection down with it."""
        with patch.dict(
            os.environ,
            {
                **self._INCLUSTER_ENV,
                "LOCAL_K8S_A2A_URL_TEMPLATE": (
                    "http://{service_name}.stable.alipay.net:{node_port}"
                ),
            },
            clear=True,
        ):
            plugin = self._incluster_plugin(mock_client, mock_core_cls)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_svc = MagicMock()
            mock_svc.spec.ports = [MagicMock(node_port=None)]
            mock_core.read_namespaced_service.return_value = mock_svc

            conn = plugin.resolve_ws_conn_info(
                paas_device_id="tpl-test-abc",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert conn.a2a_base_url == ""

    @patch("kubernetes.client.CoreV1Api")
    def test_incluster_discovery_failure_degrades_to_empty(
        self, mock_core_cls, mock_client
    ) -> None:
        """A kube api outage while resolving the A2A face must cost the A2A
        socket alone; the ws side answers through its own path unchanged."""
        with patch.dict(
            os.environ,
            {
                **self._INCLUSTER_ENV,
                "LOCAL_K8S_A2A_URL_TEMPLATE": (
                    "http://{service_name}.stable.alipay.net:{node_port}"
                ),
            },
            clear=True,
        ):
            plugin = self._incluster_plugin(mock_client, mock_core_cls)
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            mock_core.read_namespaced_service.side_effect = RuntimeError("api down")

            conn = plugin.resolve_ws_conn_info(
                paas_device_id="tpl-test-abc",
                port=8080,
                path="/api/openclaw/ws",
            )

        assert conn.a2a_base_url == ""
        assert conn.ws_url


class TestImagePullPolicy:
    """Tests for `_image_pull_policy` default behavior based on image tag."""

    @pytest.mark.parametrize(
        ("image", "expected"),
        [
            ("avernet-engine:latest", "Always"),
            ("avernet-engine", "Always"),
            ("registry.local:5000/avernet-engine:latest", "Always"),
            ("registry.local:5000/avernet-engine", "Always"),
            ("avernet-engine:1.2.3", "IfNotPresent"),
            ("registry.local:5000/avernet-engine:1.2.3", "IfNotPresent"),
        ],
    )
    def test_default_policy_depends_on_tag(self, image: str, expected: str) -> None:
        """latest or missing tag defaults to Always; explicit tag defaults to IfNotPresent."""
        with patch.dict(os.environ, {}, clear=True):
            assert _image_pull_policy(image) == expected

    @pytest.mark.parametrize(
        ("image", "env_policy", "expected"),
        [
            ("avernet-engine:latest", "IfNotPresent", "IfNotPresent"),
            ("avernet-engine:1.2.3", "Always", "Always"),
            ("avernet-engine:latest", "Never", "Never"),
        ],
    )
    def test_explicit_env_policy_overrides_default(
        self, image: str, env_policy: str, expected: str
    ) -> None:
        """LOCAL_K8S_IMAGE_PULL_POLICY is respected regardless of image tag."""
        with patch.dict(
            os.environ, {"LOCAL_K8S_IMAGE_PULL_POLICY": env_policy}, clear=True
        ):
            assert _image_pull_policy(image) == expected

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
    def test_deployment_uses_expected_policy(
        self,
        mock_core_cls,
        mock_apps_cls,
        plugin,
        mock_client,
    ) -> None:
        """End-to-end: create_sync_sandbox applies the resolved policy to containers."""
        mock_apps = MagicMock()
        mock_apps_cls.return_value = mock_apps
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        mock_pod = MagicMock()
        mock_pod.status.phase = "Running"
        mock_pod.metadata.name = "bot-pod"
        mock_core.list_namespaced_pod.return_value.items = [mock_pod]

        with patch.dict(os.environ, {}, clear=False):
            plugin.create_sync_sandbox(
                template_id="openclaw-default",
                image="custom-image:latest",
            )

        deployment = mock_apps.create_namespaced_deployment.call_args[1]["body"]
        bot_container = deployment.spec.template.spec.containers[0]
        sidecar_container = deployment.spec.template.spec.containers[1]
        assert bot_container.image_pull_policy == "Always"
        assert sidecar_container.image_pull_policy == "Always"


class _FakeWsClient:
    """模拟 kubernetes WSClient，仅实现 _exec_in_container 用到的接口。"""

    def __init__(self, error_channel: str = "") -> None:
        self._error_channel = error_channel

    def run_forever(self, timeout: float | None = None) -> None:
        pass

    def is_open(self) -> bool:
        return False

    def read_channel(self, channel: int, timeout: float | None = 0) -> str:
        if channel == 3:  # kubernetes.stream.ws_client.ERROR_CHANNEL
            data, self._error_channel = self._error_channel, ""
            return data
        return ""

    def read_stdout(self, timeout: float | None = None) -> str:
        return ""

    def read_stderr(self, timeout: float | None = None) -> str:
        return ""


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
        assert info.metadata["ip_addr"] == "10.42.0.10"

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
        mock_core.delete_namespaced_config_map.assert_called_once()

    @patch("kubernetes.client.AppsV1Api")
    @patch("kubernetes.client.CoreV1Api")
    def test_update_outbound_rule_refreshes_sidecar_without_rolling_deployment(
        self,
        mock_core_cls,
        mock_apps_cls,
        mock_client,
    ) -> None:
        """出站规则更新只刷新 envoy sidecar，不滚动 Deployment。"""
        sandbox = LocalK8sArcaSandbox(
            sandbox_id="tpl-test-abc",
            pod_name="bot-pod",
            namespace="default",
            template_id="openclaw-default",
            client=mock_client,
            container_name="bot-runtime",
            sidecar_container_name="envoy-sidecar",
        )
        sandbox._exec_in_container = MagicMock(return_value=MagicMock(exit_code=0))

        rule = OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["example.com"],
                    action="set",
                    header_name="X-Custom",
                    value="foo",
                )
            ]
        )
        assert (
            sandbox.update_outbound_rule(rule, OutBoundOperationRuleUpdatedMode.REPLACE)
            is True
        )

        mock_core = mock_core_cls.return_value
        mock_core.patch_namespaced_config_map.assert_called_once()
        mock_apps_cls.assert_not_called()

        exec_calls = sandbox._exec_in_container.call_args_list
        assert len(exec_calls) == 2
        assert all(
            call.kwargs["container_name"] == "envoy-sidecar" for call in exec_calls
        )
        assert any("quitquitquit" in arg for arg in exec_calls[0].kwargs["command"])
        assert any("ready" in arg for arg in exec_calls[1].kwargs["command"])

    @patch("kubernetes.client.CoreV1Api")
    def test_update_outbound_rule_empty_rule(self, mock_core_cls, mock_client) -> None:
        sandbox = LocalK8sArcaSandbox(
            sandbox_id="tpl-test-abc",
            pod_name="bot-pod",
            namespace="default",
            template_id="openclaw-default",
            client=mock_client,
            container_name="bot-runtime",
            sidecar_container_name="envoy-sidecar",
        )
        sandbox._exec_in_container = MagicMock(return_value=MagicMock(exit_code=0))
        assert sandbox.update_outbound_rule(None, MagicMock()) is True

    @patch("kubernetes.stream.stream")
    @patch("kubernetes.client.CoreV1Api")
    def test_exec_in_container_returns_readable_error_on_container_not_found(
        self, mock_core_cls, mock_stream, mock_client
    ) -> None:
        """容器重启空窗期 exec 应返回可读错误，而不是让 ValueError 逃逸。"""
        message = 'unable to upgrade connection: container not found ("envoy-sidecar")'
        mock_stream.return_value = _FakeWsClient(
            error_channel=json.dumps(
                {
                    "kind": "Status",
                    "status": "Failure",
                    "message": message,
                    "details": {"causes": [{"message": message}]},
                }
            )
        )
        sandbox = LocalK8sArcaSandbox(
            sandbox_id="tpl-test-abc",
            pod_name="bot-pod",
            namespace="default",
            template_id="openclaw-default",
            client=mock_client,
            container_name="bot-runtime",
        )

        result = sandbox._exec_in_container(
            container_name="envoy-sidecar",
            command=["curl", "-fsS", "http://127.0.0.1:38081/ready"],
        )

        assert result.exit_code == -1
        assert "container not found" in result.stderr

    @patch("kubernetes.stream.stream")
    @patch("kubernetes.client.CoreV1Api")
    def test_exec_in_container_parses_nonzero_exit_code(
        self, mock_core_cls, mock_stream, mock_client
    ) -> None:
        """常规失败：causes[0].message 为数字退出码时应原样返回。"""
        mock_stream.return_value = _FakeWsClient(
            error_channel=json.dumps(
                {
                    "kind": "Status",
                    "status": "Failure",
                    "message": "command terminated with non-zero exit code",
                    "details": {"causes": [{"message": "42"}]},
                }
            )
        )
        sandbox = LocalK8sArcaSandbox(
            sandbox_id="tpl-test-abc",
            pod_name="bot-pod",
            namespace="default",
            template_id="openclaw-default",
            client=mock_client,
            container_name="bot-runtime",
        )

        result = sandbox._exec_in_container(
            container_name="bot-runtime",
            command=["/bin/sh", "-c", "exit 42"],
        )

        assert result.exit_code == 42
        assert result.stderr == ""

    @patch("kubernetes.client.CoreV1Api")
    def test_update_outbound_rule_tolerates_sidecar_restart_window(
        self, mock_core_cls, mock_client
    ) -> None:
        """重启空窗期 quitquitquit 报 container not found 时应继续等待就绪。"""
        sandbox = LocalK8sArcaSandbox(
            sandbox_id="tpl-test-abc",
            pod_name="bot-pod",
            namespace="default",
            template_id="openclaw-default",
            client=mock_client,
            container_name="bot-runtime",
            sidecar_container_name="envoy-sidecar",
        )
        not_found = MagicMock(
            exit_code=-1,
            stdout="",
            stderr=(
                'unable to upgrade connection: container not found ("envoy-sidecar")'
            ),
        )
        sandbox._exec_in_container = MagicMock(
            side_effect=[not_found, MagicMock(exit_code=0)]
        )

        rule = OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["example.com"],
                    action="set",
                    header_name="X-Custom",
                    value="foo",
                )
            ]
        )
        assert (
            sandbox.update_outbound_rule(rule, OutBoundOperationRuleUpdatedMode.REPLACE)
            is True
        )
        assert sandbox._exec_in_container.call_count == 2

    @patch("kubernetes.client.CoreV1Api")
    def test_update_outbound_rule_tolerates_sidecar_admin_not_ready(
        self, mock_core_cls, mock_client
    ) -> None:
        """sidecar admin port 未就绪时 quitquitquit 报 Connection refused 应继续等待就绪。"""
        sandbox = LocalK8sArcaSandbox(
            sandbox_id="tpl-test-abc",
            pod_name="bot-pod",
            namespace="default",
            template_id="openclaw-default",
            client=mock_client,
            container_name="bot-runtime",
            sidecar_container_name="envoy-sidecar",
        )
        refused = MagicMock(
            exit_code=-1,
            stdout="",
            stderr=(
                "curl: (7) Failed to connect to 127.0.0.1 port 38081 after 3 ms: "
                "Connection refused"
            ),
        )
        sandbox._exec_in_container = MagicMock(
            side_effect=[refused, MagicMock(exit_code=0)]
        )

        rule = OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["example.com"],
                    action="set",
                    header_name="X-Custom",
                    value="foo",
                )
            ]
        )
        assert (
            sandbox.update_outbound_rule(rule, OutBoundOperationRuleUpdatedMode.REPLACE)
            is True
        )
        assert sandbox._exec_in_container.call_count == 2


class TestResolveOutboundRule:
    """Tests for _resolve_outbound_rule helper."""

    def test_resolve_outbound_rule_returns_none_when_missing(self) -> None:
        from secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin import (
            _resolve_outbound_rule,
        )

        with patch.dict(os.environ, {}, clear=True):
            assert _resolve_outbound_rule() is None

    def test_resolve_outbound_rule_returns_none_for_whitespace(self) -> None:
        from secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin import (
            _resolve_outbound_rule,
        )

        with patch.dict(
            os.environ,
            {"LOCAL_K8S_OUTBOUND_RULE": "   \n  "},
            clear=True,
        ):
            assert _resolve_outbound_rule() is None

    def test_resolve_outbound_rule_parses_valid_json(self) -> None:
        from secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin import (
            _resolve_outbound_rule,
        )

        rule = OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["example.com"],
                    action="remove",
                    header_name="X-Old",
                    value="",
                )
            ]
        )
        with patch.dict(
            os.environ,
            {"LOCAL_K8S_OUTBOUND_RULE": rule.model_dump_json()},
            clear=True,
        ):
            resolved = _resolve_outbound_rule()
            assert resolved is not None
            assert resolved.header_operation_rules is not None
            assert len(resolved.header_operation_rules) == 1
            assert resolved.header_operation_rules[0].domains == ["example.com"]
            assert resolved.header_operation_rules[0].action == "remove"

    def test_resolve_outbound_rule_raises_on_invalid_json(self) -> None:
        from secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin import (
            _resolve_outbound_rule,
        )

        with patch.dict(
            os.environ,
            {"LOCAL_K8S_OUTBOUND_RULE": "not-json"},
            clear=True,
        ):
            with pytest.raises(ValueError, match="LOCAL_K8S_OUTBOUND_RULE"):
                _resolve_outbound_rule()

    def test_resolve_outbound_rule_raises_on_bad_schema(self) -> None:
        from secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin import (
            _resolve_outbound_rule,
        )

        with patch.dict(
            os.environ,
            {
                "LOCAL_K8S_OUTBOUND_RULE": '{"header_operation_rules": [{"domains": 42}]}'
            },
            clear=True,
        ):
            with pytest.raises(ValueError, match="LOCAL_K8S_OUTBOUND_RULE"):
                _resolve_outbound_rule()


class TestInClusterConfig:
    """Tests for ServiceAccount / in-cluster config detection."""

    @patch(
        "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin.os.path.isfile",
        return_value=False,
    )
    def test_explicit_incluster_forces_true(self, _mock_isfile) -> None:
        with patch.dict(
            os.environ,
            {"LOCAL_K8S_KUBECONFIG": "in-cluster"},
            clear=True,
        ):
            assert _use_incluster_config() is True

    @patch(
        "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin.os.path.isfile",
        return_value=True,
    )
    def test_auto_incluster_when_token_present(self, _mock_isfile) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert _use_incluster_config() is True

    @patch(
        "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin.os.path.isfile",
        return_value=True,
    )
    def test_explicit_kubeconfig_disables_incluster(self, _mock_isfile) -> None:
        with patch.dict(
            os.environ,
            {"LOCAL_K8S_KUBECONFIG": "/tmp/kubeconfig"},
            clear=True,
        ):
            assert _use_incluster_config() is False

    @patch(
        "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin.os.path.isfile",
        return_value=False,
    )
    def test_no_kubeconfig_no_token_returns_false(self, _mock_isfile) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert _use_incluster_config() is False

    def test_client_manager_loads_incluster_config(self) -> None:
        manager = LocalK8sClientManager()
        with (
            patch(
                "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._use_incluster_config",
                return_value=True,
            ),
            patch(
                "kubernetes.config.load_incluster_config",
            ) as mock_load,
            patch(
                "kubernetes.client.ApiClient",
            ) as mock_api_client_cls,
        ):
            mock_api_client = MagicMock()
            mock_api_client_cls.return_value = mock_api_client

            client = manager.get_client("", None)

            mock_load.assert_called_once()
            mock_api_client_cls.assert_called_once()
            assert client is mock_api_client
            # cached on second call
            assert manager.get_client("", None) is mock_api_client
            mock_load.assert_called_once()
