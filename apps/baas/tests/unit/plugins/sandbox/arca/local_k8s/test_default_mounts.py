"""Configured local mounts use the adapter's existing storage safety boundary."""

import json
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.device_manage import ArcaCredentials, MountPoint, Storage
from secbaas.community.plugins.sandbox.arca.local_k8s import LocalK8sArcaSandboxPlugin


def plugin(monkeypatch, defaults):
    monkeypatch.setenv("LOCAL_K8S_DEFAULT_MOUNTS", json.dumps(defaults))
    monkeypatch.setenv(
        "LOCAL_K8S_PVC_MOUNT_MAPPINGS", json.dumps({"/content": "shared-content"})
    )
    monkeypatch.setenv("LOCAL_K8S_IMAGE_OWNED_MOUNTS", "[]")
    monkeypatch.setattr(
        "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
        lambda *args: "fixture",
    )
    result = LocalK8sArcaSandboxPlugin(
        credentials=ArcaCredentials(
            template_id=1,
            template_uuid="test",
            tenant_name="test",
            base_url="",
            api_key="",
        )
    )
    result._client_manager.get_client = lambda *args, **kwargs: MagicMock()
    return result


def declaration(remote="/content/repo", local="/runtime/repo", permission="ro"):
    return {"remote_dir": remote, "local_dir": local, "permission": permission}


def test_platform_default_is_a_real_read_only_mount_without_request_mounts(monkeypatch):
    adapter = plugin(monkeypatch, [declaration()])
    with (
        patch("kubernetes.client.AppsV1Api") as apps,
        patch("kubernetes.client.CoreV1Api") as core,
    ):
        pod = MagicMock()
        pod.status.phase = "Running"
        pod.metadata.name = "test-pod"
        core.return_value.list_namespaced_pod.return_value.items = [pod]
        adapter.create_sync_sandbox(template_id="test", image="test:1")
        deployment = apps.return_value.create_namespaced_deployment.call_args.kwargs[
            "body"
        ]
        mount = next(
            m
            for m in deployment.spec.template.spec.containers[0].volume_mounts
            if m.mount_path == "/runtime/repo"
        )
        assert mount.sub_path == "shared-content/repo"
        assert mount.read_only is True


@pytest.mark.parametrize(
    "defaults,requested,storage",
    [
        ([declaration(remote="/unknown/repo")], None, None),
        ([declaration(local="/runtime/../escape")], None, None),
        ([declaration(permission="invalid")], None, None),
        ([declaration(local="/opt/dev/fuse")], None, None),
        ([declaration(local="/opt/dev")], None, None),
        ([declaration(local="/opt/dev/fuse/child")], None, None),
        ([{**declaration(), "read_only": True}], None, None),
        ([declaration(), declaration(local="/runtime/repo/child")], None, None),
        ([declaration()], [MountPoint(id="", **declaration(permission="rw"))], None),
        (
            [declaration()],
            [
                MountPoint(
                    id="",
                    **declaration(
                        remote="/content/repo", local="/runtime-alias", permission="rw"
                    ),
                )
            ],
            None,
        ),
        (
            [declaration()],
            [
                MountPoint(
                    id="",
                    **declaration(
                        remote="/content", local="/runtime-alias", permission="rw"
                    ),
                )
            ],
            None,
        ),
        (
            [declaration()],
            [
                MountPoint(
                    id="",
                    **declaration(
                        remote="/content/repo/nested",
                        local="/runtime-alias",
                        permission="rw",
                    ),
                )
            ],
            None,
        ),
        (
            [declaration(local="/storage")],
            None,
            Storage(storage_id="bot", path="/storage"),
        ),
        (
            [declaration()],
            None,
            Storage(storage_id="bot", path="/runtime/repo/writable"),
        ),
        ({}, None, None),
        (None, None, None),
    ],
)
def test_invalid_defaults_and_request_conflicts_fail_before_kubernetes_writes(
    monkeypatch, defaults, requested, storage
):
    adapter = plugin(monkeypatch, defaults)
    with (
        patch("kubernetes.client.AppsV1Api") as apps,
        patch("kubernetes.client.CoreV1Api") as core,
    ):
        pod = MagicMock()
        pod.status.phase = "Running"
        pod.metadata.name = "test-pod"
        core.return_value.list_namespaced_pod.return_value.items = [pod]
        with pytest.raises(ValueError):
            adapter.create_sync_sandbox(
                template_id="test",
                image="test:1",
                mount_points=requested,
                storage=storage,
            )
        apps.assert_not_called()
        core.assert_not_called()


def test_logical_alias_cannot_write_the_same_physical_default_subpath(monkeypatch):
    adapter = plugin(monkeypatch, [declaration()])
    monkeypatch.setenv(
        "LOCAL_K8S_PVC_MOUNT_MAPPINGS",
        json.dumps(
            {
                "/content": "shared-content",
                "/alias": "shared-content/repo",
            }
        ),
    )
    with (
        patch("kubernetes.client.AppsV1Api") as apps,
        patch("kubernetes.client.CoreV1Api") as core,
    ):
        with pytest.raises(ValueError, match="read-only platform source"):
            adapter.create_sync_sandbox(
                template_id="test",
                image="test:1",
                mount_points=[
                    MountPoint(
                        id="",
                        **declaration(
                            remote="/alias", local="/writable-alias", permission="rw"
                        ),
                    ),
                ],
            )
        apps.assert_not_called()
        core.assert_not_called()


@pytest.mark.parametrize(
    "storage_id", ["shared-content/repo", "shared-content", "shared-content/repo/child"]
)
def test_bot_storage_id_cannot_alias_a_protected_pvc_source(monkeypatch, storage_id):
    adapter = plugin(monkeypatch, [declaration()])
    with (
        patch("kubernetes.client.AppsV1Api") as apps,
        patch("kubernetes.client.CoreV1Api") as core,
    ):
        with pytest.raises(ValueError, match="read-only platform source"):
            adapter.create_sync_sandbox(
                template_id="test",
                image="test:1",
                storage=Storage(storage_id=storage_id, path="/writable-alias"),
            )
        apps.assert_not_called()
        core.assert_not_called()


def test_separate_bot_data_stays_writable_beside_the_read_only_default(monkeypatch):
    adapter = plugin(monkeypatch, [declaration()])
    with (
        patch("kubernetes.client.AppsV1Api") as apps,
        patch("kubernetes.client.CoreV1Api") as core,
    ):
        pod = MagicMock()
        pod.status.phase = "Running"
        pod.metadata.name = "test-pod"
        core.return_value.list_namespaced_pod.return_value.items = [pod]
        adapter.create_sync_sandbox(
            template_id="test",
            image="test:1",
            storage=Storage(storage_id="sessions", path="/home/admin"),
            mount_points=[
                MountPoint(
                    id="",
                    **declaration(
                        remote="/content/bot-data", local="/data", permission="rw"
                    ),
                ),
            ],
        )
        deployment = apps.return_value.create_namespaced_deployment.call_args.kwargs[
            "body"
        ]
        mounts = deployment.spec.template.spec.containers[0].volume_mounts
        assert (
            next(m for m in mounts if m.mount_path == "/runtime/repo").read_only is True
        )
        assert next(m for m in mounts if m.mount_path == "/data").read_only is False
