"""本地 K8s Arca 沙箱插件实现。

基于本地 Kubernetes 集群（colima / k3d / kind / minikube 等）模拟 Arca 沙箱生命周期。
使用 kubeconfig 连接集群，通过 Deployment + NodePort Service 暴露 bot runtime。
所有 local_k8s 专用参数默认从环境变量读取，避免修改 API 模型。

环境变量：

| 环境变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|
| LOCAL_K8S_KUBECONFIG | $HOME/.kube/config | 否 | kubeconfig 文件路径 |
| LOCAL_K8S_CONTEXT | colima | 否 | 使用的 kubeconfig context |
| LOCAL_K8S_NAMESPACE | default | 否 | 目标 namespace |
| LOCAL_K8S_IMAGE | local-k8s-openclaw:latest | 否 | bot 容器镜像，可被 docker_image 入参覆盖 |
| LOCAL_K8S_CONTAINER_PORT | 20003 | 否 | 容器暴露端口 |
| LOCAL_K8S_CPU_REQUEST | 500m | 否 | CPU request |
| LOCAL_K8S_CPU_LIMIT | 1 | 否 | CPU limit |
| LOCAL_K8S_MEMORY_REQUEST | 1Gi | 否 | 内存 request |
| LOCAL_K8S_MEMORY_LIMIT | 2Gi | 否 | 内存 limit |
| LOCAL_K8S_IMAGE_PULL_POLICY | IfNotPresent | 否 | 镜像拉取策略 |
| LOCAL_K8S_NODE_PORT | - | 否 | 固定 NodePort；不填则由 K8s 自动分配 |
| LOCAL_K8S_EXTRA_ENVS | - | 否 | JSON 对象，注入到 Pod 容器的环境变量（可被 envs 入参覆盖） |

colima + k3s 最小示例（NodePort，无需额外映射端口）：
::

    export LOCAL_K8S_KUBECONFIG="$HOME/.kube/config"
    export LOCAL_K8S_CONTEXT="colima"
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import yaml

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    MountPoint,
    OutBoundOperationRule,
    ResourceSpecification,
    Storage,
)
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.arca import ArcaSandbox, ArcaSandboxPlugin

from ._sandbox import LocalK8sArcaSandbox

if TYPE_CHECKING:
    from kubernetes.client import ApiClient, AppsV1Api, CoreV1Api

    from secbaas.community.api.device_manage import ArcaCredentials

logger = get_logger("plugin-sandbox")

_SANDBOX_ID_DELIMITER = "--"
_DEFAULT_CONTAINER_PORT = 20003
_DEFAULT_CPU_REQUEST = "500m"
_DEFAULT_CPU_LIMIT = "1"
_DEFAULT_MEMORY_REQUEST = "1Gi"
_DEFAULT_MEMORY_LIMIT = "2Gi"

_DEFAULT_IMAGE = "local-k8s-openclaw:latest"
_DEFAULT_CONTEXT = "colima"

# Environment variable names consumed by this plugin.
ENV_KUBECONFIG = "LOCAL_K8S_KUBECONFIG"
_ENV_FALLBACK_KUBECONFIG_PATH = "KUBECONFIG"
ENV_CONTEXT = "LOCAL_K8S_CONTEXT"
ENV_NAMESPACE = "LOCAL_K8S_NAMESPACE"
ENV_IMAGE = "LOCAL_K8S_IMAGE"
ENV_CONTAINER_PORT = "LOCAL_K8S_CONTAINER_PORT"
ENV_CPU_REQUEST = "LOCAL_K8S_CPU_REQUEST"
ENV_CPU_LIMIT = "LOCAL_K8S_CPU_LIMIT"
ENV_MEMORY_REQUEST = "LOCAL_K8S_MEMORY_REQUEST"
ENV_MEMORY_LIMIT = "LOCAL_K8S_MEMORY_LIMIT"
ENV_IMAGE_PULL_POLICY = "LOCAL_K8S_IMAGE_PULL_POLICY"
ENV_NODE_PORT = "LOCAL_K8S_NODE_PORT"
ENV_EXTRA_ENVS = "LOCAL_K8S_EXTRA_ENVS"


def _env(name: str, default: Any = None) -> Any:
    """Read an environment variable with a default."""
    return os.environ.get(name, default)


def _env_int(name: str, default: int | None = None) -> int | None:
    """Read an integer environment variable with a default."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"local_k8s: {name} must be an integer, got {raw!r}") from exc


def _sanitize_name(name: str) -> str:
    """把任意名称转换成 RFC 1123 子域格式。"""
    sanitized = re.sub(r"[^a-z0-9-]", "-", name.lower())
    sanitized = re.sub(r"-+", "-", sanitized).strip("-.")
    return sanitized or "local-k8s-sandbox"


def _build_deployment_name(template_id: str) -> str:
    """生成 Deployment 名称。"""
    uid = uuid.uuid4().hex[:12]
    base = _sanitize_name(template_id)[:30]
    return f"{base}-{uid}"


def _build_service_name(deployment_name: str) -> str:
    return f"{deployment_name}-svc"


def _resolve_image(image_override: str | None) -> str:
    """沙箱镜像优先级：显式参数 > LOCAL_K8S_IMAGE 环境变量 > 内置默认值。"""
    image = image_override or _env(ENV_IMAGE, _DEFAULT_IMAGE)
    if not image:
        raise ValueError(
            "local_k8s plugin requires an image. "
            "Provide it via ArcaCreateConfig.docker_image or set LOCAL_K8S_IMAGE."
        )
    return image


def _resolve_kubeconfig() -> str:
    """读取 kubeconfig 内容：LOCAL_K8S_KUBECONFIG > KUBECONFIG > ~/.kube/config。"""
    path = _env(ENV_KUBECONFIG) or _env(_ENV_FALLBACK_KUBECONFIG_PATH)
    default_path = os.path.expanduser("~/.kube/config")
    if path:
        path = os.path.expandvars(os.path.expanduser(path))
    else:
        path = default_path

    if not os.path.isfile(path):
        raise ValueError(
            f"local_k8s plugin requires kubeconfig, but no readable file found at {path}. "
            f"Set LOCAL_K8S_KUBECONFIG, KUBECONFIG, or place config at {default_path}."
        )
    with open(path, encoding="utf-8") as f:
        return f.read()


def _context() -> str:
    return _env(ENV_CONTEXT, _DEFAULT_CONTEXT)


def _namespace() -> str:
    return _env(ENV_NAMESPACE, "default")


def _container_port() -> int:
    return _env_int(ENV_CONTAINER_PORT, _DEFAULT_CONTAINER_PORT) or _DEFAULT_CONTAINER_PORT


def _image_pull_policy() -> str:
    return _env(ENV_IMAGE_PULL_POLICY, "IfNotPresent")


def _node_port() -> int | None:
    return _env_int(ENV_NODE_PORT)


def _resolve_extra_envs() -> dict[str, str]:
    """读取 LOCAL_K8S_EXTRA_ENVS（JSON 对象）作为额外环境变量。

    示例：
        LOCAL_K8S_EXTRA_ENVS='{"FOO": "bar", "BAZ": "qux"}'
    """
    raw = _env(ENV_EXTRA_ENVS)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"local_k8s: {ENV_EXTRA_ENVS} must be a JSON object, got {raw!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"local_k8s: {ENV_EXTRA_ENVS} must be a JSON object, "
            f"got {type(parsed).__name__}"
        )
    return {str(k): str(v) for k, v in parsed.items()}


def _build_resources(
    resource_spec: ResourceSpecification | None,
) -> tuple[str, str, str, str]:
    """返回 (cpu_request, cpu_limit, memory_request, memory_limit)。"""
    if resource_spec is None:
        return (
            _env(ENV_CPU_REQUEST, _DEFAULT_CPU_REQUEST),
            _env(ENV_CPU_LIMIT, _DEFAULT_CPU_LIMIT),
            _env(ENV_MEMORY_REQUEST, _DEFAULT_MEMORY_REQUEST),
            _env(ENV_MEMORY_LIMIT, _DEFAULT_MEMORY_LIMIT),
        )
    return (
        str(resource_spec.cpu),
        str(resource_spec.cpu),
        f"{resource_spec.memory}Gi",
        f"{resource_spec.memory}Gi",
    )


def _env_list_from_dict(envs: dict[str, str] | None) -> list[Any]:
    """把 env dict 转成 V1EnvVar 列表。"""
    if not envs:
        return []
    from kubernetes.client import V1EnvVar

    return [V1EnvVar(name=k, value=v) for k, v in envs.items()]


class LocalK8sClientManager:
    """本地 K8s ApiClient 生命周期管理。

    根据 kubeconfig 内容懒加载 ApiClient，按 kubeconfig 内容缓存。
    """

    def __init__(self) -> None:
        self._lock = None  # 本插件同步调用，暂时不需要线程锁
        self._clients: dict[str, Any] = {}

    def get_client(self, kubeconfig: str, context: str | None = None) -> ApiClient:
        """获取或创建 ApiClient。"""
        from kubernetes import config as k8s_config

        key = f"{context or ''}:{hash(kubeconfig) & 0xFFFFFFFF}"
        client = self._clients.get(key)
        if client is not None:
            return client

        config_dict = yaml.safe_load(kubeconfig)
        client = k8s_config.new_client_from_config_dict(
            config_dict=config_dict,
            context=context,
            persist_config=False,
        )
        self._clients[key] = client
        logger.info("local_k8s: created new ApiClient (context=%s)", context or "current")
        return client

    def close(self) -> None:
        for client in self._clients.values():
            try:
                client.close()
            except Exception:
                pass
        self._clients.clear()


class LocalK8sArcaSandboxPlugin(ArcaSandboxPlugin):
    """本地 K8s Arca 沙箱插件。

    基于本地 Kubernetes 集群运行 bot runtime，
    通过 NodePort Service 把端口暴露到 localhost。

    插件需要的所有本地 K8s 参数默认从同名 LOCAL_K8S_* 环境变量读取；
    ArcaCredentials 仅保留基础模板/认证信息，不再扩展 local_k8s 字段。

    完整参数与默认值参见本模块顶部 docstring。
    """

    CONTAINER_NAME = "bot-runtime"

    def __init__(
        self,
        credentials: ArcaCredentials | None = None,
    ) -> None:
        self._credentials = credentials
        self._client_manager = LocalK8sClientManager()

    def _client(self) -> ApiClient:
        return self._client_manager.get_client(_resolve_kubeconfig(), _context())

    def _create_deployment(
        self,
        deployment_name: str,
        template_id: str,
        image: str,
        container_port: int,
        envs: dict[str, str] | None,
        resource_spec: ResourceSpecification | None,
    ) -> None:
        """创建 Deployment。"""
        from kubernetes.client import (
            AppsV1Api,
            V1Container,
            V1Deployment,
            V1DeploymentSpec,
            V1LabelSelector,
            V1ObjectMeta,
            V1PodSpec,
            V1PodTemplateSpec,
            V1ResourceRequirements,
        )

        namespace = _namespace()
        labels = {
            "app": deployment_name,
            "sandbox-id": deployment_name,
            "template-id": _sanitize_name(template_id),
            "managed-by": "secbaas-local-k8s",
        }
        cpu_request, cpu_limit, memory_request, memory_limit = _build_resources(
            resource_spec
        )

        container = V1Container(
            name=self.CONTAINER_NAME,
            image=image,
            image_pull_policy=_image_pull_policy(),
            ports=[{"containerPort": container_port}],  # type: ignore[arg-type]
            env=_env_list_from_dict(envs),
            resources=V1ResourceRequirements(
                requests={"cpu": cpu_request, "memory": memory_request},
                limits={"cpu": cpu_limit, "memory": memory_limit},
            ),
        )

        deployment = V1Deployment(
            api_version="apps/v1",
            kind="Deployment",
            metadata=V1ObjectMeta(name=deployment_name, labels=labels),
            spec=V1DeploymentSpec(
                replicas=1,
                selector=V1LabelSelector(match_labels={"app": deployment_name}),
                template=V1PodTemplateSpec(
                    metadata=V1ObjectMeta(labels=labels),
                    spec=V1PodSpec(containers=[container]),
                ),
            ),
        )

        apps_api = AppsV1Api(self._client())
        apps_api.create_namespaced_deployment(namespace=namespace, body=deployment)
        logger.info("local_k8s: created deployment %s/%s", namespace, deployment_name)

    def _create_service(
        self,
        deployment_name: str,
        container_port: int,
    ) -> None:
        """创建 NodePort Service 暴露容器端口。"""
        from kubernetes.client import (
            CoreV1Api,
            V1ObjectMeta,
            V1Service,
            V1ServicePort,
            V1ServiceSpec,
        )

        namespace = _namespace()
        service_name = _build_service_name(deployment_name)

        port_kwargs: dict[str, Any] = {
            "port": container_port,
            "target_port": container_port,
        }
        node_port = _node_port()
        if node_port is not None:
            port_kwargs["node_port"] = node_port

        service = V1Service(
            api_version="v1",
            kind="Service",
            metadata=V1ObjectMeta(
                name=service_name, labels={"app": deployment_name}
            ),
            spec=V1ServiceSpec(
                type="NodePort",
                selector={"app": deployment_name},
                ports=[V1ServicePort(**port_kwargs)],
            ),
        )

        core_api = CoreV1Api(self._client())
        core_api.create_namespaced_service(namespace=namespace, body=service)
        logger.info(
            "local_k8s: created NodePort service %s/%s",
            namespace,
            service_name,
        )

    def _wait_for_pod(
        self,
        deployment_name: str,
        timeout_seconds: int,
    ) -> str:
        """等待该 Deployment 下的 Pod 进入 Running。返回 Pod 名。"""
        from kubernetes.client import CoreV1Api

        namespace = _namespace()
        core_api = CoreV1Api(self._client())
        label_selector = f"app={deployment_name}"
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            pods = core_api.list_namespaced_pod(
                namespace=namespace, label_selector=label_selector
            )
            if pods.items:
                pod = pods.items[0]
                if pod.status and pod.status.phase == "Running":
                    return pod.metadata.name
            time.sleep(1.0)

        raise RuntimeError(
            f"local_k8s: pod for deployment {deployment_name} "
            f"did not become Running within {timeout_seconds}s"
        )

    def _find_pod_name(self, deployment_name: str) -> str | None:
        """查询 Deployment 下当前 Pod 名。"""
        from kubernetes.client import CoreV1Api

        namespace = _namespace()
        core_api = CoreV1Api(self._client())
        pods = core_api.list_namespaced_pod(
            namespace=namespace, label_selector=f"app={deployment_name}"
        )
        return pods.items[0].metadata.name if pods.items else None

    def _resolve_public_port(self, deployment_name: str) -> int:
        """解析外部可访问端口（读取 Service 自动分配的 NodePort）。"""
        from kubernetes.client import CoreV1Api

        namespace = _namespace()
        service_name = _build_service_name(deployment_name)
        core_api = CoreV1Api(self._client())

        svc = core_api.read_namespaced_service(
            name=service_name, namespace=namespace
        )
        if not svc.spec or not svc.spec.ports:
            raise RuntimeError("local_k8s: service has no ports")
        node_port = svc.spec.ports[0].node_port
        if not node_port:
            raise RuntimeError("local_k8s: service NodePort is not assigned")
        return node_port

    def create_sync_sandbox(
        self,
        template_id: str,
        ttl_in_minutes: int | None = None,
        envs: dict[str, str] | None = None,
        mount_points: list[MountPoint] | None = None,
        resource_spec: ResourceSpecification | None = None,
        metadata: dict[str, str] | None = None,
        outbound_operation_rule: OutBoundOperationRule | None = None,
        storage: Storage | None = None,
        image: str | None = None,
        timeout_in_millis: int = 60000,
        ready_timeout_in_seconds: int = 60,
    ) -> ArcaSandbox:
        """创建本地 K8s 沙箱。"""
        deployment_name = _build_deployment_name(template_id)
        container_port = _container_port()
        resolved_image = _resolve_image(image)

        # 合并环境变量。优先级：调用方 envs > LOCAL_K8S_EXTRA_ENVS > metadata 默认值
        merged_envs = _resolve_extra_envs()
        merged_envs.update(envs or {})
        if metadata:
            merged_envs.setdefault("BOT_ID", metadata.get("bot_id", ""))

        self._create_deployment(
            deployment_name=deployment_name,
            template_id=template_id,
            image=resolved_image,
            container_port=container_port,
            envs=merged_envs,
            resource_spec=resource_spec,
        )
        self._create_service(deployment_name, container_port)
        pod_name = self._wait_for_pod(deployment_name, ready_timeout_in_seconds)

        return LocalK8sArcaSandbox(
            sandbox_id=deployment_name,
            pod_name=pod_name,
            namespace=_namespace(),
            template_id=template_id,
            client=self._client(),
            container_name=self.CONTAINER_NAME,
            credentials=self._credentials,
            ttl_in_minutes=ttl_in_minutes,
            resources=resource_spec,
        )

    def connect_sync_sandbox(self, sandbox_id: str) -> ArcaSandbox:
        """连接到已存在的沙箱。"""
        pod_name = self._find_pod_name(sandbox_id)
        if not pod_name:
            raise RuntimeError(f"local_k8s: no pod found for sandbox {sandbox_id}")
        return LocalK8sArcaSandbox(
            sandbox_id=sandbox_id,
            pod_name=pod_name,
            namespace=_namespace(),
            template_id=sandbox_id,  # 重连时不知道原始 template_id，用 sandbox_id 占位
            client=self._client(),
            container_name=self.CONTAINER_NAME,
            credentials=self._credentials,
        )

    def close(self) -> None:
        """释放 ApiClient。"""
        self._client_manager.close()

    def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        template_id: int | None = None,
    ) -> WsConnectionInfo:
        """解析 WebSocket 连接信息。

        本地模式下返回 localhost 可访问地址。
        """
        deployment_name = paas_device_id.split(_SANDBOX_ID_DELIMITER, maxsplit=1)[0]
        public_port = self._resolve_public_port(deployment_name)
        normalized_path = "/" + path.lstrip("/")
        return WsConnectionInfo(
            ws_url=f"ws://localhost:{public_port}{normalized_path}",
            token="",
            target=f"localhost:{public_port}",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )

    def resolve_http_connection_info(
        self,
        paas_device_id: str,
        port: int,
        path: str = "/",
        template_id: int | None = None,
    ) -> HttpConnectionInfo:
        """解析 HTTP 连接信息。"""
        deployment_name = paas_device_id.split(_SANDBOX_ID_DELIMITER, maxsplit=1)[0]
        public_port = self._resolve_public_port(deployment_name)
        normalized_path = "/" + path.lstrip("/")
        return HttpConnectionInfo(
            http_url=f"http://localhost:{public_port}{normalized_path}",
            token="",
            target=f"localhost:{public_port}",
        )

    def delete_storage(self, storage_id: str, tenant_name: str) -> bool:
        """本地模式无 NAS 存储，直接返回 True。"""
        logger.info(
            "local_k8s: delete_storage storage_id=%s tenant_name=%s (no-op)",
            storage_id,
            tenant_name,
        )
        return True
