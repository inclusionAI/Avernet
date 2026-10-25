"""Actual local adapter deployment wiring, without a Kubernetes cluster."""

import json
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.device_manage import ArcaCredentials, MountPoint, Storage
from secbaas.community.plugins.sandbox.arca.local_k8s import LocalK8sArcaSandboxPlugin
from secbaas.community.plugins.sandbox.arca.local_k8s._volumes import (
    build_volumes,
    resolve_mounts,
)

MAPPINGS = {"/artifacts": "published/bolt_data"}
IMAGE_MOUNTS = [{"remote_dir": "/sys", "local_dir": "/mnt/sys", "permission": "ro"}]


def point(
    remote="/artifacts/staff_1/bot", local="/home/admin/nfs/bot-data", permission="rw"
):
    return MountPoint(
        id="test", remote_dir=remote, local_dir=local, permission=permission
    )


@pytest.mark.parametrize("permission,read_only", [("ro", True), ("rw", False)])
def test_adapter_translates_mount_points_into_deployment(
    monkeypatch, permission, read_only
):
    monkeypatch.setattr(
        "secbaas.community.plugins.sandbox.arca.local_k8s._sandbox_plugin._resolve_kubeconfig",
        lambda *args: "fixture",
    )
    monkeypatch.setenv("LOCAL_K8S_PVC_MOUNT_MAPPINGS", json.dumps(MAPPINGS))
    monkeypatch.setenv("LOCAL_K8S_IMAGE_OWNED_MOUNTS", json.dumps(IMAGE_MOUNTS))
    plugin = LocalK8sArcaSandboxPlugin(
        credentials=ArcaCredentials(
            template_id=1,
            template_uuid="test",
            tenant_name="test",
            base_url="",
            api_key="",
        )
    )
    plugin._client_manager.get_client = lambda *args, **kwargs: MagicMock()
    with (
        patch("kubernetes.client.AppsV1Api") as apps,
        patch("kubernetes.client.CoreV1Api") as core,
    ):
        pod = MagicMock()
        pod.status.phase = "Running"
        pod.metadata.name = "test-pod"
        core.return_value.list_namespaced_pod.return_value.items = [pod]
        plugin.create_sync_sandbox(
            template_id="test",
            image="test:1",
            storage=Storage(storage_id="sessions", path="/home/admin/.openclaw/agents"),
            mount_points=[
                point(permission=permission),
                point("/sys", "/mnt/sys", "ro"),
            ],
        )
        deployment = apps.return_value.create_namespaced_deployment.call_args.kwargs[
            "body"
        ]
        spec = deployment.spec.template.spec
        mounts = spec.containers[0].volume_mounts
        artifact = next(m for m in mounts if m.mount_path.endswith("bot-data"))
        assert artifact.sub_path == "published/bolt_data/staff_1/bot"
        assert artifact.read_only is read_only
        assert not any(m.mount_path == "/mnt/sys" for m in mounts)
        assert (
            next(
                v for v in spec.volumes if v.name == artifact.name
            ).persistent_volume_claim.claim_name
            == "bot-shared-data"
        )


@pytest.mark.parametrize(
    "remote,local",
    [
        ("/unknown", "/data"),
        ("/artifacts/../escape", "/data"),
        ("/artifacts-other/bot", "/data"),
        ("/artifacts/bot", "relative"),
        ("/artifacts/bot", "/"),
        ("/artifacts//bot", "/data"),
    ],
)
def test_rejects_unsupported_or_unsafe_mounts(remote, local):
    with pytest.raises(ValueError):
        resolve_mounts([point(remote, local)], MAPPINGS, IMAGE_MOUNTS)


def test_image_owned_mount_requires_exact_permission():
    with pytest.raises(ValueError, match="unsupported"):
        resolve_mounts([point("/sys", "/mnt/sys", "rw")], MAPPINGS, IMAGE_MOUNTS)


def test_rejects_invalid_mapping_and_conflicts():
    for mapping in (
        {"/artifacts": "../escape"},
        {"/artifacts": "/absolute"},
        {"/artifacts": "published", "/artifacts/child": "other"},
    ):
        with pytest.raises(ValueError):
            resolve_mounts([], mapping, [])
    with pytest.raises(ValueError, match="duplicate"):
        resolve_mounts([point(), point()], MAPPINGS, [])
    with pytest.raises(ValueError, match="conflict"):
        build_volumes(
            "headers",
            "pvc",
            "bot",
            "/home/admin",
            [("/home/admin", "published/bot", False)],
        )


def test_mount_validation_precedes_kubernetes_writes(monkeypatch):
    monkeypatch.setenv("LOCAL_K8S_PVC_MOUNT_MAPPINGS", json.dumps(MAPPINGS))
    plugin = LocalK8sArcaSandboxPlugin(
        credentials=ArcaCredentials(
            template_id=1,
            template_uuid="test",
            tenant_name="test",
            base_url="",
            api_key="",
        )
    )
    with (
        patch("kubernetes.client.CoreV1Api") as core,
        patch("kubernetes.client.AppsV1Api") as apps,
    ):
        with pytest.raises(ValueError, match="unsupported"):
            plugin.create_sync_sandbox(
                template_id="test", image="test:1", mount_points=[point("/missing")]
            )
        core.assert_not_called()
        apps.assert_not_called()
