"""Translate explicitly configured local storage mappings into Kubernetes volumes.

This is local infrastructure only: remote_dir remains a logical platform path,
not a host path or an implicit request to mount a production filesystem.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from secbaas.community.api.device_manage import MountPermission, MountPoint

if TYPE_CHECKING:
    from kubernetes.client import V1Container, V1Volume, V1VolumeMount

SIDECAR_PROXY_PORT = 38080
SIDECAR_ADMIN_PORT = 38081
_FUSE_DEVICE_MOUNT_PATH = PurePosixPath("/opt/dev/fuse")


def _absolute_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value.startswith("/") or value.startswith("//") or ".." in value.split("/"):
        raise ValueError("local_k8s: mount paths must be absolute without traversal")
    if str(path) != value.rstrip("/") or path == PurePosixPath("/"):
        raise ValueError("local_k8s: mount paths must be canonical and non-root")
    return path


def parse_default_mounts(value: object) -> list[MountPoint]:
    """Parse platform defaults without broadening the request mount grammar."""
    if not isinstance(value, list):
        raise ValueError("local_k8s: default mounts must be a list")
    mounts = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "remote_dir",
            "local_dir",
            "permission",
        }:
            raise ValueError("local_k8s: invalid default mount declaration")
        mounts.append(MountPoint(id="", **item))
    return mounts


def resolve_mounts(
    mount_points: list[MountPoint] | None,
    mappings: dict[str, str],
    image_mounts: list[dict[str, str]],
    *,
    default_mounts: list[MountPoint] | None = None,
    storage_id: str | None = None,
) -> list[tuple[str, str, bool]]:
    """Fail closed unless a mount maps to the PVC or an exact image-owned mount."""
    if not isinstance(mappings, dict) or not isinstance(image_mounts, list):
        raise ValueError(
            "local_k8s: mount mappings must be an object and image mounts a list"
        )
    prefixes: list[tuple[PurePosixPath, str]] = []
    for remote, sub_path in mappings.items():
        prefix = _absolute_path(remote)
        sub = PurePosixPath(sub_path)
        if (
            sub.is_absolute()
            or not sub.parts
            or ".." in sub.parts
            or str(sub) != sub_path
        ):
            raise ValueError("local_k8s: PVC mapping must use a safe relative subPath")
        if any(
            prefix.is_relative_to(p) or p.is_relative_to(prefix) for p, _ in prefixes
        ):
            raise ValueError("local_k8s: overlapping remote mount prefixes")
        prefixes.append((prefix, sub_path))
    delegated = set()
    for item in image_mounts:
        if set(item) != {"remote_dir", "local_dir", "permission"}:
            raise ValueError("local_k8s: invalid image-owned mount declaration")
        _absolute_path(item["remote_dir"])
        _absolute_path(item["local_dir"])
        MountPermission(item["permission"])
        delegated.add((item["remote_dir"], item["local_dir"], item["permission"]))
    resolved = []
    platform_sources = []
    request_sources = []
    defaults = default_mounts or []
    destinations: set[PurePosixPath] = {_FUSE_DEVICE_MOUNT_PATH}
    for index, mount in enumerate([*defaults, *(mount_points or [])]):
        remote, local = (
            _absolute_path(mount.remote_dir),
            _absolute_path(mount.local_dir),
        )
        if any(
            local.is_relative_to(other) or other.is_relative_to(local)
            for other in destinations
        ):
            raise ValueError("local_k8s: duplicate or overlapping mount destination")
        destinations.add(local)
        if (mount.remote_dir, mount.local_dir, mount.permission.value) in delegated:
            continue
        match = next(
            ((p, sub) for p, sub in prefixes if remote.is_relative_to(p)), None
        )
        if match is None:
            raise ValueError(f"local_k8s: unsupported remote mount: {mount.remote_dir}")
        prefix, sub_path = match
        item = (
            str(local),
            str(PurePosixPath(sub_path) / remote.relative_to(prefix)),
            mount.permission == MountPermission.READ_ONLY,
        )
        resolved.append(item)
        (platform_sources if index < len(defaults) else request_sources).append(item)
    protected = [
        PurePosixPath(sub) for _, sub, read_only in platform_sources if read_only
    ]
    writable = [
        PurePosixPath(sub) for _, sub, read_only in request_sources if not read_only
    ]
    if storage_id:
        writable.append(PurePosixPath(storage_id))
    if any(
        source.is_relative_to(guard) or guard.is_relative_to(source)
        for source in writable
        for guard in protected
    ):
        raise ValueError(
            "local_k8s: writable storage overlaps a read-only platform source"
        )
    return resolved


def validate_storage_mounts(
    storage_id: str | None,
    mount_path: str,
    extra_mounts: list[tuple[str, str, bool]],
) -> None:
    """Validate effective per-bot storage before any Kubernetes resource writes."""
    if not storage_id:
        return
    if PurePosixPath(storage_id).is_absolute() or ".." in storage_id.split("/"):
        raise ValueError("local_k8s: unsafe storage_id")
    storage_path = _absolute_path(mount_path)
    if storage_path.is_relative_to(
        _FUSE_DEVICE_MOUNT_PATH
    ) or _FUSE_DEVICE_MOUNT_PATH.is_relative_to(storage_path):
        raise ValueError("local_k8s: bot storage conflicts with the FUSE device mount")
    if any(
        storage_path.is_relative_to(PurePosixPath(destination))
        for destination, _, _ in extra_mounts
    ):
        raise ValueError("local_k8s: mount conflicts with bot storage")


def build_volumes(
    configmap_name: str,
    pvc_name: str | None,
    storage_id: str | None,
    mount_path: str,
    extra_mounts: list[tuple[str, str, bool]],
) -> tuple[list[V1Volume], list[V1VolumeMount]]:
    validate_storage_mounts(storage_id, mount_path, extra_mounts)
    from kubernetes.client import (
        V1ConfigMapVolumeSource,
        V1HostPathVolumeSource,
        V1PersistentVolumeClaimVolumeSource,
        V1Volume,
        V1VolumeMount,
    )

    mounts = [
        V1VolumeMount(name="fuse-device", mount_path=str(_FUSE_DEVICE_MOUNT_PATH))
    ]
    volumes = [
        V1Volume(
            name="header-rules", config_map=V1ConfigMapVolumeSource(name=configmap_name)
        )
    ]
    if pvc_name:
        volumes.append(
            V1Volume(
                name="bot-data",
                persistent_volume_claim=V1PersistentVolumeClaimVolumeSource(
                    claim_name=pvc_name
                ),
            )
        )
        if storage_id:
            mounts.append(
                V1VolumeMount(
                    name="bot-data", mount_path=mount_path, sub_path=storage_id
                )
            )
        for destination, sub_path, read_only in extra_mounts:
            mounts.append(
                V1VolumeMount(
                    name="bot-data",
                    mount_path=destination,
                    sub_path=sub_path,
                    read_only=read_only,
                )
            )
    elif extra_mounts:
        raise ValueError("local_k8s: extra mounts require a shared PVC")
    volumes.append(
        V1Volume(
            name="fuse-device",
            host_path=V1HostPathVolumeSource(path="/dev/fuse", type="CharDevice"),
        )
    )
    return volumes, mounts


def build_sidecar(
    image: str, pull_policy: str, name: str, rules_path: str
) -> V1Container:
    """Build the unchanged header-rules sidecar adjacent to its volume wiring."""
    from kubernetes.client import (
        V1Capabilities,
        V1Container,
        V1EnvVar,
        V1ResourceRequirements,
        V1SecurityContext,
        V1VolumeMount,
    )

    return V1Container(
        name=name,
        image=image,
        image_pull_policy=pull_policy,
        ports=[
            {"containerPort": SIDECAR_PROXY_PORT},
            {"containerPort": SIDECAR_ADMIN_PORT},
        ],
        env=[V1EnvVar(name="HEADER_RULES", value=rules_path)],
        volume_mounts=[
            V1VolumeMount(
                name="header-rules",
                mount_path="/etc/sidecar/header-rules",
                read_only=True,
            )
        ],
        resources=V1ResourceRequirements(
            requests={"cpu": "50m", "memory": "64Mi"},
            limits={"cpu": "200m", "memory": "256Mi"},
        ),
        security_context=V1SecurityContext(
            capabilities=V1Capabilities(add=["NET_ADMIN"])
        ),
    )
