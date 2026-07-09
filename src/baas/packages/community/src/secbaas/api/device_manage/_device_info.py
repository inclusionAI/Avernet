"""Device info models.

Defines polymorphic device info hierarchy for querying device status
across different PaaS platforms (Arca, Sigma, etc.).
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class DeviceInfo(BaseModel):
    """Base device info - platform-agnostic.

    Only contains fields common to all PaaS platforms.
    Platform-specific fields belong in subclasses.
    """

    platform: Literal["arca", "sigma", "local", "poolab", "teclaw", "docker", "k8s"] = (
        Field(..., description="平台类型: arca|sigma|local|poolab|teclaw|docker|k8s")
    )
    status: str = Field(..., description="设备状态")


class ArcaDeviceInfo(DeviceInfo):
    """Arca platform-specific device info from SDK get_info().

    Contains all fields returned by Arca SDK sandbox.get_info() method.
    """

    sandbox_id: str = Field(..., description="沙箱ID")
    template_id: str = Field(..., description="模板ID")
    ip_address: str | None = Field(default=None, description="内网IP")
    ttl_seconds: int = Field(..., description="剩余存活时间(秒)")
    ttl_timestamp: int | None = Field(default=None, description="过期时间戳（毫秒级）")
    created_at: datetime = Field(..., description="创建时间")
    name: str | None = Field(default=None, description="沙箱名称")
    description: str | None = Field(default=None, description="描述")
    resource_spec: dict[str, Any] | None = Field(
        default=None, description="资源配置(CPU/内存等)"
    )
    mount_points: list[dict[str, Any]] | None = Field(
        default=None, description="挂载点信息"
    )
    envs: dict[str, str] | None = Field(default=None, description="环境变量")


class SigmaDeviceInfo(DeviceInfo):
    """Sigma platform-specific device info.

    Stub class for future Sigma platform implementation.
    """

    region: str | None = Field(default=None, description="区域")
    instance_id: str | None = Field(default=None, description="Sigma实例ID")
    status_detail: dict[str, Any] | None = Field(default=None, description="状态详情")


class LocalDeviceInfo(DeviceInfo):
    """Local platform-specific device info from mng daemon.

    Contains fields describing a container on a local machine.
    """

    container_id: str = Field(description="容器ID")
    machine_id: str = Field(description="机器ID")
    user_id: str = Field(description="用户ID")
    port: int = Field(description="端口")


class PoolabDeviceInfo(DeviceInfo):
    """Poolab platform-specific device info from getMachine API.

    Mirrors the getMachine API response shape (same as createMachine response).
    """

    platform: Literal["poolab"] = Field(default="poolab", description="平台类型")
    poolab_id: str = Field(..., description="Poolab machine ID")
    poolab_user_id: str = Field(..., description="User ID")
    poolab_hostname: str | None = Field(default=None, description="Machine hostname")
    poolab_image_id: str | None = Field(default=None, description="Image ID")
    poolab_status: str | None = Field(default=None, description="Machine status")
    poolab_openclaw_url: str | None = Field(default=None, description="OpenClaw URL")
    poolab_openclaw_token: str | None = Field(
        default=None, description="OpenClaw access token"
    )
    poolab_operations_url: str | None = Field(
        default=None, description="Operations page URL"
    )
    poolab_remote_url: str | None = Field(
        default=None, description="Remote desktop URL"
    )
    poolab_display_status: str | None = Field(
        default=None, description="Display status"
    )
    poolab_type: str | None = Field(default=None, description="Machine type")
    poolab_network_type: str | None = Field(default=None, description="Network type")
    poolab_env: str | None = Field(default=None, description="Environment (e.g., TEST)")


class TeClawDeviceInfo(DeviceInfo):
    """TeClaw platform-specific device info from REST API.

    Mirrors the TeClaw REST API getBot response shape.
    """

    platform: Literal["teclaw"] = Field(default="teclaw", description="平台类型")
    teclaw_bot_id: str = Field(..., description="TeClaw bot ID")
    online_teclaw_bot_config: dict[str, Any] | None = Field(
        default=None, description="在线 bot 配置"
    )
    gray_teclaw_bot_config: dict[str, Any] | None = Field(
        default=None, description="灰度 bot 配置"
    )
    gray_strategy: dict[str, Any] | None = Field(default=None, description="灰度策略")


class DockerDeviceInfo(DeviceInfo):
    """Docker platform-specific device info from Docker SDK.

    Per DOCKER_API-08: 4 fields (platform, container_id, host_port, image)
    plus inherited status from DeviceInfo.
    """

    platform: Literal["docker"] = Field(default="docker", description="平台类型")
    container_id: str = Field(..., description="Docker 容器 ID")
    host_port: int = Field(..., ge=1, le=65535, description="宿主机映射端口")
    image: str = Field(..., description="容器使用的镜像名称")


class PodInfo(BaseModel):
    """Per-Pod container status details.

    Maps to V1ContainerStatus fields from the K8s API:
    - name: container name (str)
    - ready: readiness probe result (bool)
    - restart_count: number of restarts (int)
    - state: container state string, e.g. "running", "waiting", "terminated" (str | None)
    - image: container image string (str | None)
    """

    name: str = Field(description="Container name from V1ContainerStatus.name")
    ready: bool = Field(
        description="Readiness probe result from V1ContainerStatus.ready"
    )
    restart_count: int = Field(
        description="Number of container restarts from V1ContainerStatus.restart_count",
    )
    state: str | None = Field(
        default=None,
        description="Container state (running/waiting/terminated) from V1ContainerStatus.state",
    )
    image: str | None = Field(
        default=None, description="Container image string from V1ContainerStatus.image"
    )


class K8sDeviceInfo(DeviceInfo):
    """K8s platform-specific device info.

    Contains deployment and pod details for a K8s-backed device.
    """

    platform: Literal["k8s"] = Field(default="k8s", description="平台类型")
    status: str = Field(description="Device status (RUNNING, TERMINATING, etc.)")
    deployment_name: str = Field(description="K8s Deployment name")
    namespace: str = Field(description="K8s namespace")
    pod_ip: str | None = Field(default=None, description="Pod IP address")
    pods: list[PodInfo] | None = Field(
        default=None, description="Per-Pod container status details (D-10)"
    )
