"""本地 K8s Arca 沙箱插件实现。

基于本地 Kubernetes 集群（colima / k3d / kind / minikube 等）模拟 Arca 沙箱生命周期。
使用 kubeconfig 连接集群，通过 Deployment + NodePort Service 暴露 bot runtime。

环境变量：

| 环境变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|
| LOCAL_K8S_KUBECONFIG | $HOME/.kube/config | 否 | kubeconfig 文件路径；设为 ``in-cluster`` 时强制使用 Pod ServiceAccount |
| LOCAL_K8S_CONTEXT | colima | 否 | 使用的 kubeconfig context |
| LOCAL_K8S_NAMESPACE | default | 否 | 目标 namespace |
| LOCAL_K8S_IMAGE | local-k8s-openclaw:latest | 否 | bot 容器镜像，可被 docker_image 入参覆盖 |
| LOCAL_K8S_CONTAINER_PORT | 20003 | 否 | 容器暴露端口 |
| LOCAL_K8S_CPU_REQUEST | 500m | 否 | CPU request |
| LOCAL_K8S_CPU_LIMIT | 1 | 否 | CPU limit |
| LOCAL_K8S_IMAGE_PULL_POLICY | - | 否 | 显式镜像拉取策略；未设置时，镜像 tag 为 ``latest`` 或为空则默认 ``Always``，否则默认 ``IfNotPresent`` |
| LOCAL_K8S_NODE_PORT | - | 否 | 固定 NodePort；不填则由 K8s 自动分配 |
| LOCAL_K8S_EXTRA_ENVS | - | 否 | JSON 对象，注入到 Pod 容器的环境变量（可被 envs 入参覆盖） |
| LOCAL_K8S_OUTBOUND_RULE | - | 否 | JSON 对象，作为 ``OutBoundOperationRule`` 的环境变量 fallback |
| LOCAL_K8S_SIDECAR_IMAGE | avernet-engine-sidecar:latest | 否 | engine sidecar 镜像 |
| LOCAL_K8S_SHARED_PVC_NAME | bot-shared-data | 否 | 共享 PVC 名称 |
| LOCAL_K8S_SHARED_PVC_SIZE | 10Gi | 否 | 共享 PVC 总大小 |
| LOCAL_K8S_SHARED_PVC_HOST_MOUNT_PATH | - | 否 | 插件 Pod 上共享 PVC 的挂载路径；配置后 delete_storage 会删除该 bot 的 subPath 目录 |
| LOCAL_K8S_APPARMOR_PROFILE | arca-fuse | 否 | privileged bot runtime 使用的 host AppArmor profile；必须允许 FUSE mount 且保持 enforce |

Mount mapping configuration is documented in the adjacent README.md.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    MountPoint,
    OutBoundOperationRule,
    ResourceSpecification,
    Storage,
)
from secbaas.community.logger import get_logger
from secbaas.community.plugins.sandbox.utils.arca_utils import ArcaUtils
from secbaas.community.spi.sandbox.arca import ArcaSandbox, ArcaSandboxPlugin

from . import _volumes
from ._sandbox import LocalK8sArcaSandbox, _convert_outbound_rules

if TYPE_CHECKING:
    from kubernetes.client import ApiClient

    from secbaas.community.api.device_manage import ArcaCredentials

logger = get_logger("plugin-sandbox")

_SANDBOX_ID_DELIMITER = "--"
_DEFAULT_CONTAINER_PORT = 20003
_DEFAULT_CPU_REQUEST = "500m"
_DEFAULT_CPU_LIMIT = "1"
_DEFAULT_MEMORY_REQUEST = "1Gi"
_DEFAULT_MEMORY_LIMIT = "2Gi"

_DEFAULT_IMAGE = "avernet-engine:latest"
_DEFAULT_SIDECAR_IMAGE = "avernet-engine-sidecar:latest"
_DEFAULT_CONTEXT = "colima"

_SIDECAR_CONTAINER_NAME = "envoy-sidecar"
_SIDECAR_HEADER_RULES_PATH = "/etc/sidecar/header-rules/header-rules.yaml"

_BOT_DATA_MOUNT_PATH = "/home/admin"
_STORAGE_ID_ANNOTATION = "secbaas.local-k8s.io/storage-id"

# Shared PVC used for all local_k8s bot data. Each bot gets a distinct subPath
# inside this PVC, so backend can mount the same PVC at /home/admin/.merge_nas
# and read {storage_id} without any code changes on the backend side.
_SHARED_BOT_DATA_PVC_NAME = "bot-shared-data"
_SHARED_BOT_DATA_PVC_SIZE = "10Gi"
_SHARED_BOT_DATA_PVC_ACCESS_MODE = "ReadWriteOnce"

# a2a 地址发现的 kube 实查上界（review P2：A2A 可选字段绝不拖住 WS 签发——
# to_thread 挪出事件循环之外的第二道防线）；NodePort 记忆化在插件实例上（
# Service 生命周期内不变），重复签发零 kube RT
_A2A_URL_DISCOVERY_TIMEOUT_S = 5.0
_A2A_URL_DISCOVERY_NEGATIVE_TTL_S = 30.0

# Environment variable names consumed by this plugin.
ENV_KUBECONFIG = "LOCAL_K8S_KUBECONFIG"
_ENV_FALLBACK_KUBECONFIG_PATH = "KUBECONFIG"
ENV_CONTEXT = "LOCAL_K8S_CONTEXT"
ENV_NAMESPACE = "LOCAL_K8S_NAMESPACE"
ENV_IMAGE = "LOCAL_K8S_IMAGE"
ENV_SIDECAR_IMAGE = "LOCAL_K8S_SIDECAR_IMAGE"
ENV_CONTAINER_PORT = "LOCAL_K8S_CONTAINER_PORT"
ENV_CPU_REQUEST = "LOCAL_K8S_CPU_REQUEST"
ENV_CPU_LIMIT = "LOCAL_K8S_CPU_LIMIT"
ENV_MEMORY_REQUEST = "LOCAL_K8S_MEMORY_REQUEST"
ENV_MEMORY_LIMIT = "LOCAL_K8S_MEMORY_LIMIT"
ENV_IMAGE_PULL_POLICY = "LOCAL_K8S_IMAGE_PULL_POLICY"
ENV_NODE_PORT = "LOCAL_K8S_NODE_PORT"
ENV_EXTRA_ENVS = "LOCAL_K8S_EXTRA_ENVS"
ENV_OUTBOUND_RULE = "LOCAL_K8S_OUTBOUND_RULE"
ENV_SHARED_PVC_NAME = "LOCAL_K8S_SHARED_PVC_NAME"
ENV_SHARED_PVC_SIZE = "LOCAL_K8S_SHARED_PVC_SIZE"
ENV_SHARED_PVC_HOST_MOUNT_PATH = "LOCAL_K8S_SHARED_PVC_HOST_MOUNT_PATH"
ENV_APPARMOR_PROFILE = "LOCAL_K8S_APPARMOR_PROFILE"
# A2A 面（agent-card / rpc）的「浏览器可达」URL 模板，仅 out-of-cluster 之外的
# in-cluster 部署需要（in-cluster 的 BaaS 自己只能看到 ClusterIP；NodePort 的
# 端口号经 kube api 实查，host 形态是部署事实，不属于本插件的猜测范围）。
# 占位符：{service_name}（如 ``<deployment>-svc``）、{namespace}、{node_port}。
ENV_A2A_URL_TEMPLATE = "LOCAL_K8S_A2A_URL_TEMPLATE"

_INCLUSTER_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"


def _use_incluster_config() -> bool:
    """判断是否使用 Pod ServiceAccount 的 in-cluster 配置。

    显式设置 ``LOCAL_K8S_KUBECONFIG=in-cluster`` 时强制启用；
    否则当没有配置 kubeconfig 且 ServiceAccount token 文件存在时自动启用。
    """
    explicit = _env(ENV_KUBECONFIG)
    if explicit and explicit.strip().lower() == "in-cluster":
        return True
    has_explicit_kubeconfig = bool(
        _env(ENV_KUBECONFIG) or _env(_ENV_FALLBACK_KUBECONFIG_PATH)
    )
    return not has_explicit_kubeconfig and os.path.isfile(_INCLUSTER_TOKEN_PATH)


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


def _shared_pvc_name() -> str:
    """返回共享 PVC 名称，可通过环境变量覆盖。"""
    return _env(ENV_SHARED_PVC_NAME) or _SHARED_BOT_DATA_PVC_NAME


def _shared_pvc_size() -> str:
    """返回共享 PVC 大小，可通过环境变量覆盖。"""
    return _env(ENV_SHARED_PVC_SIZE) or _SHARED_BOT_DATA_PVC_SIZE


def _apparmor_profile_name() -> str:
    """返回 bot runtime 使用的 host AppArmor profile 名。

    该 profile 需要允许 bot runtime 使用 ``/dev/fuse`` 完成 FUSE 挂载，
    同时保持受限执行语义。
    """
    profile = _env(ENV_APPARMOR_PROFILE, "arca-fuse")
    if profile is None or not str(profile).strip():
        raise ValueError(
            "local_k8s: LOCAL_K8S_APPARMOR_PROFILE must not be empty. "
            "Use arca-fuse or another AppArmor profile that permits mount."
        )
    return str(profile).strip()


def _resolve_image(image_override: str | None) -> str:
    """沙箱镜像优先级：显式参数 > LOCAL_K8S_IMAGE 环境变量 > 内置默认值。"""
    image = image_override or _env(ENV_IMAGE, _DEFAULT_IMAGE)
    if not image:
        raise ValueError(
            "local_k8s plugin requires an image. "
            "Provide it via ArcaCreateConfig.docker_image or set LOCAL_K8S_IMAGE."
        )
    return image


def _resolve_sidecar_image(image_override: str | None = None) -> str:
    """Sidecar 镜像优先级：显式参数 > LOCAL_K8S_SIDECAR_IMAGE 环境变量 > 内置默认值。"""
    image = image_override or _env(ENV_SIDECAR_IMAGE, _DEFAULT_SIDECAR_IMAGE)
    if not image:
        raise ValueError(
            "local_k8s plugin requires a sidecar image. "
            "Provide it via config or set LOCAL_K8S_SIDECAR_IMAGE."
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
    return (
        _env_int(ENV_CONTAINER_PORT, _DEFAULT_CONTAINER_PORT) or _DEFAULT_CONTAINER_PORT
    )


def _image_pull_policy(image: str) -> str:
    """根据用户显式配置和镜像 tag 决定 imagePullPolicy。

    优先级：
    1. ``LOCAL_K8S_IMAGE_PULL_POLICY`` 环境变量显式设置时，直接采用。
    2. 镜像 tag 为 ``latest`` 或镜像未指定 tag 时，使用 ``Always``，
       避免 K8s 复用本地过期的 ``latest`` 镜像。
    3. 其它明确 tag 使用 ``IfNotPresent``，减少重复拉取。
    """
    env_policy = _env(ENV_IMAGE_PULL_POLICY)
    if env_policy:
        return env_policy
    tag = _extract_image_tag(image)
    if not tag or tag == "latest":
        return "Always"
    return "IfNotPresent"


def _extract_image_tag(image: str) -> str:
    """从容器镜像引用中提取 tag（不含 digest 部分）。

    支持 ``registry:port/namespace/image:tag`` 形式，正确区分_registry_端口和 tag。
    """
    # Strip digest if present; tag precedes '@'.
    name_part = image.split("@", 1)[0]
    # Tag is the last ':' after the last '/'.
    last_slash = name_part.rfind("/")
    name_and_tag = name_part[last_slash + 1 :]
    if ":" in name_and_tag:
        _, tag = name_and_tag.rsplit(":", 1)
        return tag
    return ""


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


def _resolve_outbound_rule() -> OutBoundOperationRule | None:
    """读取 LOCAL_K8S_OUTBOUND_RULE（JSON 对象）作为 outbound rule 的 fallback。

    示例：
        LOCAL_K8S_OUTBOUND_RULE='{"header_operation_rules": [
            {"domains": ["example.com"], "action": "set",
             "header_name": "X-Token", "value": "secret"}
        ]}'
    """
    raw = _env(ENV_OUTBOUND_RULE)
    if not raw or not raw.strip():
        return None
    try:
        return OutBoundOperationRule.model_validate_json(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"local_k8s: {ENV_OUTBOUND_RULE} must be a valid JSON object"
        ) from exc
    except ValidationError as exc:
        raise ValueError(
            f"local_k8s: {ENV_OUTBOUND_RULE} does not match "
            f"OutBoundOperationRule schema"
        ) from exc


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
        self._incluster_loaded = False

    def get_client(self, kubeconfig: str, context: str | None = None) -> ApiClient:
        """获取或创建 ApiClient。"""
        from kubernetes import config as k8s_config

        if _use_incluster_config():
            if not self._incluster_loaded:
                k8s_config.load_incluster_config()
                self._incluster_loaded = True
                logger.info("local_k8s: loaded in-cluster config from ServiceAccount")

            client = self._clients.get("incluster")
            if client is not None:
                return client

            from kubernetes.client import ApiClient

            client = ApiClient()
            self._clients["incluster"] = client
            logger.info("local_k8s: created in-cluster ApiClient")
            return client

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
        logger.info(
            "local_k8s: created new ApiClient (context=%s)", context or "current"
        )
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
    通过 NodePort Service（out-of-cluster）或 ClusterIP + Service 端口
    （in-cluster）暴露 bot runtime。

    插件需要的所有本地 K8s 参数默认从同名 LOCAL_K8S_* 环境变量读取；
    ArcaCredentials 仅保留基础模板/认证信息，不再扩展 local_k8s 字段。

    完整参数与默认值参见本模块顶部 docstring。
    """

    CONTAINER_NAME = "bot-runtime"

    def __init__(
        self,
        credentials: ArcaCredentials | None = None,
        arca_utils: ArcaUtils | None = None,
    ) -> None:
        self._credentials = credentials
        self._arca_utils = arca_utils
        # (namespace, service_name) -> node_port|None|('error', monotonic_ts)
        # 300 系后两字段是隔离语义的执行面（见 _a2a_base_url_cached）：
        # in-flight 防线程轰炸；线程表供 a2a_discovery_join（测试/诊断面）
        self._a2a_node_port_cache: dict[tuple[str, str], int | None | tuple[str, float]]
        self._a2a_node_port_cache = {}
        self._a2a_inflight: set[tuple[str, str]] = set()
        self._a2a_threads: dict[tuple[str, str], "threading.Thread"] = {}
        self._client_manager = LocalK8sClientManager()

    def _client(self) -> ApiClient:
        if _use_incluster_config():
            return self._client_manager.get_client("", None)
        return self._client_manager.get_client(_resolve_kubeconfig(), _context())

    def _create_header_rules_configmap(
        self,
        deployment_name: str,
        outbound_operation_rule: OutBoundOperationRule | None,
    ) -> str:
        """创建 header-rules ConfigMap，返回 ConfigMap 名称。"""
        from kubernetes.client import CoreV1Api, V1ConfigMap, V1ObjectMeta

        namespace = _namespace()
        configmap_name = f"envoy-header-rules-{deployment_name}"
        configmap = V1ConfigMap(
            api_version="v1",
            kind="ConfigMap",
            metadata=V1ObjectMeta(name=configmap_name, namespace=namespace),
            data={
                "header-rules.yaml": _convert_outbound_rules(outbound_operation_rule)
            },
        )
        core_api = CoreV1Api(self._client())
        core_api.create_namespaced_config_map(namespace=namespace, body=configmap)
        logger.info("local_k8s: created configmap %s/%s", namespace, configmap_name)
        return configmap_name

    def _create_shared_bot_data_pvc(self) -> str:
        """创建（或复用）共享 PVC，所有 bot 的 subPath 都在其下。

        幂等：PVC 已存在时直接返回现有名称，避免重复创建导致冲突。
        """
        from kubernetes.client import (
            ApiException,
            CoreV1Api,
            V1ObjectMeta,
            V1PersistentVolumeClaim,
            V1PersistentVolumeClaimSpec,
            V1ResourceRequirements,
        )

        namespace = _namespace()
        pvc_name = _shared_pvc_name()
        core_api = CoreV1Api(self._client())

        try:
            core_api.read_namespaced_persistent_volume_claim(
                name=pvc_name, namespace=namespace
            )
            logger.info(
                "local_k8s: shared pvc %s/%s already exists, reuse", namespace, pvc_name
            )
            return pvc_name
        except ApiException as e:
            if e.status == 404:
                pass
            else:
                raise

        size = _shared_pvc_size()
        pvc = V1PersistentVolumeClaim(
            api_version="v1",
            kind="PersistentVolumeClaim",
            metadata=V1ObjectMeta(name=pvc_name, namespace=namespace),
            spec=V1PersistentVolumeClaimSpec(
                access_modes=[_SHARED_BOT_DATA_PVC_ACCESS_MODE],
                resources=V1ResourceRequirements(requests={"storage": size}),
            ),
        )
        core_api.create_namespaced_persistent_volume_claim(
            namespace=namespace, body=pvc
        )
        logger.info("local_k8s: created shared pvc %s/%s", namespace, pvc_name)
        return pvc_name

    def _create_deployment(
        self,
        deployment_name: str,
        template_id: str,
        image: str,
        sidecar_image: str,
        container_port: int,
        envs: dict[str, str] | None,
        resource_spec: ResourceSpecification | None,
        configmap_name: str,
        pvc_name: str | None = None,
        storage_id: str | None = None,
        mount_path: str = _BOT_DATA_MOUNT_PATH,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> None:
        """创建包含 bot-runtime 与 envoy-sidecar 的双容器 Deployment。"""
        from kubernetes.client import (
            AppsV1Api,
            V1AppArmorProfile,
            V1Container,
            V1Deployment,
            V1DeploymentSpec,
            V1LabelSelector,
            V1ObjectMeta,
            V1PodSpec,
            V1PodTemplateSpec,
            V1ResourceRequirements,
            V1SecurityContext,
        )

        namespace = _namespace()
        labels = {
            "app": deployment_name,
            "sandbox-id": deployment_name,
            "template-id": _sanitize_name(template_id),
            "managed-by": "secbaas-local-k8s",
        }
        annotations = {_STORAGE_ID_ANNOTATION: storage_id} if storage_id else None
        cpu_request, cpu_limit, memory_request, memory_limit = _build_resources(
            resource_spec
        )

        volumes, volume_mounts = _volumes.build_volumes(
            configmap_name, pvc_name, storage_id, mount_path, extra_mounts or []
        )

        container = V1Container(
            name=self.CONTAINER_NAME,
            image=image,
            image_pull_policy=_image_pull_policy(image),
            ports=[{"containerPort": container_port}],  # type: ignore[arg-type]
            env=_env_list_from_dict(envs),
            resources=V1ResourceRequirements(
                requests={"cpu": cpu_request, "memory": memory_request},
                limits={"cpu": cpu_limit, "memory": memory_limit},
            ),
            # privileged grants /dev/fuse device-cgroup access needed by the
            # bot runtime's FUSE mounts. The configured localhost profile
            # must permit those mounts while remaining enforced.
            security_context=V1SecurityContext(
                privileged=True,
                app_armor_profile=V1AppArmorProfile(
                    type="Localhost",
                    localhost_profile=_apparmor_profile_name(),
                ),
            ),
            volume_mounts=volume_mounts,
        )

        sidecar = _volumes.build_sidecar(
            sidecar_image,
            _image_pull_policy(sidecar_image),
            _SIDECAR_CONTAINER_NAME,
            _SIDECAR_HEADER_RULES_PATH,
        )

        deployment = V1Deployment(
            api_version="apps/v1",
            kind="Deployment",
            metadata=V1ObjectMeta(
                name=deployment_name, labels=labels, annotations=annotations
            ),
            spec=V1DeploymentSpec(
                replicas=1,
                selector=V1LabelSelector(match_labels={"app": deployment_name}),
                template=V1PodTemplateSpec(
                    metadata=V1ObjectMeta(labels=labels, annotations=annotations),
                    spec=V1PodSpec(containers=[container, sidecar], volumes=volumes),
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
            metadata=V1ObjectMeta(name=service_name, labels={"app": deployment_name}),
            spec=V1ServiceSpec(
                type="NodePort",
                selector={"app": deployment_name},
                ports=[V1ServicePort(**port_kwargs)],
            ),
        )

        core_api = CoreV1Api(self._client())
        created = core_api.create_namespaced_service(
            namespace=namespace, body=service
        )
        # A2A 地址预热（review P2 终闭环）：create 响应里 API server 已完成
        # NodePort 分配——直接落 a2a 记忆化，后续签发零 kube 查询。响应缺值
        #（异步分配边缘）时留给后台发现补齐，不影响签发。
        try:
            created_ports = (
                getattr(created, "spec", None) and created.spec.ports or None
            )
            assigned = created_ports[0].node_port if created_ports else None
            if assigned:
                self._a2a_node_port_cache[(namespace, service_name)] = assigned
        except Exception:  # noqa: BLE001 — 预热失败无关紧要（还有后台发现）
            pass
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

    def _resolve_public_port(self, deployment_name: str) -> tuple[str, int]:
        """解析外部可访问地址和端口。

        in-cluster 模式下返回 ClusterIP + Service 端口；
        out-of-cluster 模式下返回 localhost + NodePort。
        """
        from kubernetes.client import CoreV1Api

        namespace = _namespace()
        service_name = _build_service_name(deployment_name)
        core_api = CoreV1Api(self._client())

        svc = core_api.read_namespaced_service(name=service_name, namespace=namespace)
        if not svc.spec or not svc.spec.ports:
            raise RuntimeError("local_k8s: service has no ports")
        svc_port = svc.spec.ports[0]
        if _use_incluster_config():
            return svc.spec.cluster_ip or "127.0.0.1", svc_port.port
        node_port = svc_port.node_port
        if not node_port:
            raise RuntimeError("local_k8s: service NodePort is not assigned")
        return "localhost", node_port

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
        resolved_sidecar_image = _resolve_sidecar_image()
        extra_mounts = _volumes.resolve_mounts(
            mount_points,
            json.loads(_env("LOCAL_K8S_PVC_MOUNT_MAPPINGS", "{}")),
            json.loads(_env("LOCAL_K8S_IMAGE_OWNED_MOUNTS", "[]")),
        )

        # 合并环境变量。优先级：调用方 envs > LOCAL_K8S_EXTRA_ENVS > metadata 默认值
        merged_envs = _resolve_extra_envs()
        merged_envs.update(envs or {})

        # 优先使用 BaaS 侧 Storage 参数（与 backend 各 composer 对齐），
        # 否则退回到 metadata.bot_id（独立使用插件时）。
        storage_id: str | None = None
        if storage and storage.storage_id:
            storage_id = storage.storage_id
        elif metadata and metadata.get("bot_id"):
            storage_id = metadata["bot_id"]

        if metadata:
            merged_envs.setdefault("BOT_ID", metadata.get("bot_id", ""))

        # 优先使用入参中的 outbound rule，否则回退到 LOCAL_K8S_OUTBOUND_RULE 环境变量。
        effective_rule = outbound_operation_rule or _resolve_outbound_rule()

        # 如果携带 storage_id，则在共享 PVC 中按 storage_id 分 subPath。
        mount_path = storage.path if storage and storage.path else _BOT_DATA_MOUNT_PATH
        _volumes.validate_storage_mounts(storage_id, mount_path, extra_mounts)
        quota = (
            storage.quota if storage and storage.quota else _SHARED_BOT_DATA_PVC_SIZE
        )
        pvc_name = (
            self._create_shared_bot_data_pvc() if storage_id or extra_mounts else None
        )
        effective_storage = (
            Storage(
                storage_id=storage_id,
                type="pvc",
                path=mount_path,
                quota=quota,
            )
            if storage_id
            else None
        )

        configmap_name = self._create_header_rules_configmap(
            deployment_name=deployment_name,
            outbound_operation_rule=effective_rule,
        )
        self._create_deployment(
            deployment_name=deployment_name,
            template_id=template_id,
            image=resolved_image,
            sidecar_image=resolved_sidecar_image,
            container_port=container_port,
            envs=merged_envs,
            resource_spec=resource_spec,
            configmap_name=configmap_name,
            pvc_name=pvc_name,
            storage_id=storage_id,
            mount_path=mount_path,
            extra_mounts=extra_mounts,
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
            sidecar_container_name=_SIDECAR_CONTAINER_NAME,
            pvc_name=pvc_name,
            storage=effective_storage,
        )

    def _resolve_storage_id_from_deployment(self, deployment_name: str) -> str | None:
        """读取 Deployment 注解，恢复 storage id。"""
        from kubernetes.client import ApiException, AppsV1Api

        namespace = _namespace()
        apps_api = AppsV1Api(self._client())
        try:
            deployment = apps_api.read_namespaced_deployment(
                name=deployment_name, namespace=namespace
            )
        except ApiException as e:
            if e.status == 404:
                return None
            raise
        annotations = (
            deployment.metadata.annotations if deployment.metadata else None
        ) or {}
        storage_id = annotations.get(_STORAGE_ID_ANNOTATION)
        return storage_id if isinstance(storage_id, str) else None

    def connect_sync_sandbox(self, sandbox_id: str) -> ArcaSandbox:
        """连接到已存在的沙箱。"""
        pod_name = self._find_pod_name(sandbox_id)
        if not pod_name:
            raise RuntimeError(f"local_k8s: no pod found for sandbox {sandbox_id}")
        recovered_storage_id = self._resolve_storage_id_from_deployment(sandbox_id)
        pvc_name = _shared_pvc_name() if recovered_storage_id else None
        storage = (
            Storage(
                storage_id=recovered_storage_id,
                type="pvc",
                path=_BOT_DATA_MOUNT_PATH,
            )
            if recovered_storage_id
            else None
        )
        return LocalK8sArcaSandbox(
            sandbox_id=sandbox_id,
            pod_name=pod_name,
            namespace=_namespace(),
            template_id=sandbox_id,  # 重连时不知道原始 template_id，用 sandbox_id 占位
            client=self._client(),
            container_name=self.CONTAINER_NAME,
            credentials=self._credentials,
            sidecar_container_name=_SIDECAR_CONTAINER_NAME,
            pvc_name=pvc_name,
            storage=storage,
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

        in-cluster 模式下使用 ClusterIP + Service 端口供集群内部访问；
        out-of-cluster 模式下使用 localhost + NodePort。
        两种模式都附带 ``a2a_base_url``（本插件是少数定义了「浏览器可达」
        A2A 地址语义的平台；详见 :meth:`_resolve_a2a_base_url`）。
        """
        deployment_name = paas_device_id
        normalized_path = "/" + path.lstrip("/")
        if self._arca_utils is not None and _use_incluster_config():
            target = self._arca_utils._get_arca_target(
                deployment_name, port=port, template_id=template_id
            )
            return WsConnectionInfo(
                ws_url=self._arca_utils.build_proxypass_url(
                    target, normalized_path, scheme="ws"
                ),
                token=self._arca_utils._get_proxypass_token(
                    deployment_name, port=port, template_id=template_id, ttl=120
                ),
                target=target,
                expires_at=datetime.now(UTC) + timedelta(seconds=120),
                a2a_base_url=self._a2a_base_url_cached(deployment_name),
            )
        host, svc_port = self._resolve_public_port(deployment_name)
        # out-of-cluster：NodePort 就映射在 BaaS 自己的机器上，ws url 用的
        # 已经是浏览器可达地址，A2A 面同源直连（无凭据、根级路径）。
        return WsConnectionInfo(
            ws_url=f"ws://{host}:{svc_port}{normalized_path}",
            token="",
            target=f"{host}:{svc_port}",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            a2a_base_url=f"http://{host}:{svc_port}",
        )

    def _a2a_template(self) -> str:
        """部署级 A2A host 约定（LOCAL_K8S_A2A_URL_TEMPLATE）。空=未配置。"""
        return str(_env(ENV_A2A_URL_TEMPLATE) or "").strip()

    def _a2a_base_url_cached(self, deployment_name: str) -> str:
        """in-cluster 下取本设备 A2A 面的「浏览器可达」HTTP base——**签发
        关键路径只读本方法，零 kube 查询**（review P2 终闭环：可选地址发现
        慢/挂/不可用时，存量 WS 签发毫秒级照常返回，最多缺一个加性字段）。

        语义与 ``_resolve_a2a_base_url`` 旧文档一致：地址由模板 + Service
        NodePort 组成；模板空、NodePort 未知/未配、查询失败——一律返回
        空串（"本平台当前无可交付的 A2A 地址"），绝不让 A2A 的配置缺陷
        拖住 WS 签发。NodePort 由 *后台发现线程* 维护进 ``_a2a_node_port_cache``
        （创建 Service 时亦会从 create 响应预热）；缓存未就绪的第一笔签发
        得到空地址，后台发现完成后后续签发自然带上。
        """
        template = self._a2a_template()
        if not template:
            return ""
        namespace = _namespace()
        service_name = _build_service_name(deployment_name)
        cache_key = (namespace, service_name)

        entry = self._a2a_node_port_cache.get(cache_key)
        if isinstance(entry, tuple):
            kind, expire_at = entry
            if kind == "error" and time.monotonic() >= expire_at:
                # 负缓存过了 TTL：可以再探一次（仍然后台，不阻塞本签发）
                self._a2a_node_port_cache.pop(cache_key, None)
                entry = None
            else:
                return ""
        if isinstance(entry, int):
            return self._a2a_render(template, namespace, service_name, entry)
        # 未就绪（无值/None=已知无 NodePort）：值面可用即返回，缺值即调度
        # 后台发现（in-flight 防轰炸），本签发立即以空地址返回
        if entry is None and cache_key in self._a2a_node_port_cache:
            return ""
        self._a2a_schedule_discovery(namespace, service_name)
        return ""

    @staticmethod
    def _a2a_render(
        template: str, namespace: str, service_name: str, node_port: int,
    ) -> str:
        return (
            template
            .replace("{service_name}", service_name)
            .replace("{namespace}", namespace)
            .replace("{node_port}", str(node_port))
        )

    def _a2a_schedule_discovery(self, namespace: str, service_name: str) -> None:
        """后台发现 NodePort（守护线程、in-flight 去重）。失败写负缓存
        （短 TTL），持续故障下不会反复等待、也不会线程轰炸。"""
        cache_key = (namespace, service_name)
        if cache_key in self._a2a_inflight:
            return
        self._a2a_inflight.add(cache_key)
        thread = threading.Thread(
            target=self._a2a_discover,
            args=(namespace, service_name),
            daemon=True,
            name=f"a2a-url-discovery-{service_name}",
        )
        self._a2a_threads[cache_key] = thread
        thread.start()

    def _a2a_discover(self, namespace: str, service_name: str) -> None:
        from kubernetes.client import CoreV1Api

        cache_key = (namespace, service_name)
        try:
            core_api = CoreV1Api(self._client())
            svc = core_api.read_namespaced_service(
                name=service_name, namespace=namespace,
                _request_timeout=_A2A_URL_DISCOVERY_TIMEOUT_S,
            )
            node_port = (
                svc.spec.ports[0].node_port
                if svc.spec and svc.spec.ports
                else None
            )
            self._a2a_node_port_cache[cache_key] = node_port
            if not node_port:
                logger.warning(
                    "local_k8s: service %s/%s has no NodePort; a2a base url omitted",
                    namespace, service_name,
                )
        except Exception as exc:  # noqa: BLE001 — 发现失败降级为空地址 + 负缓存
            logger.warning(
                "local_k8s: a2a base url discovery failed for %s/%s: %s",
                namespace, service_name, exc,
            )
            self._a2a_node_port_cache[cache_key] = (
                "error", time.monotonic() + _A2A_URL_DISCOVERY_NEGATIVE_TTL_S,
            )
        finally:
            self._a2a_inflight.discard(cache_key)

    def a2a_discovery_join(self, deployment_name: str, timeout: float = 5.0) -> None:
        """等待在途后台发现完成（测试/诊断 seam；产品路径不等待）。"""
        cache_key = (_namespace(), _build_service_name(deployment_name))
        thread = self._a2a_threads.pop(cache_key, None)
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def resolve_http_connection_info(
        self,
        paas_device_id: str,
        port: int,
        path: str = "/",
        template_id: int | None = None,
    ) -> HttpConnectionInfo:
        """解析 HTTP 连接信息。

        in-cluster 模式下使用 ClusterIP + Service 端口供集群内部访问；
        out-of-cluster 模式下使用 localhost + NodePort。
        """
        deployment_name = paas_device_id
        host, svc_port = self._resolve_public_port(deployment_name)
        normalized_path = "/" + path.lstrip("/")
        return HttpConnectionInfo(
            http_url=f"http://{host}:{svc_port}{normalized_path}",
            token="",
            target=f"{host}:{svc_port}",
        )

    def delete_storage(self, storage_id: str, tenant_name: str) -> bool:
        """删除 bot 在共享 PVC 中的 subPath 目录。

        当 ``LOCAL_K8S_SHARED_PVC_HOST_MOUNT_PATH`` 未配置时，插件没有共享 PVC
        的文件系统入口，退化为幂等 no-op（与 local_proc 保持一致）。
        配置后，会删除 ``{host_mount_path}/{storage_id}`` 目录。
        """
        if not storage_id:
            return True

        host_mount_path = _env(ENV_SHARED_PVC_HOST_MOUNT_PATH)
        if not host_mount_path:
            logger.info(
                "local_k8s: delete_storage storage_id=%s tenant_name=%s "
                "(shared PVC host mount path not configured, no-op)",
                storage_id,
                tenant_name,
            )
            return True

        import shutil

        real_host = os.path.realpath(host_mount_path)
        target = os.path.join(host_mount_path, storage_id)
        real_target = os.path.realpath(target)

        # 安全校验：禁止删除挂载点本身或路径逃逸到挂载点之外。
        if real_target == real_host:
            logger.warning(
                "local_k8s: refuse to delete shared PVC host mount path itself: %s",
                real_host,
            )
            return False
        if not real_target.startswith(real_host + os.sep):
            logger.warning(
                "local_k8s: refuse to delete path outside shared PVC mount: %s",
                real_target,
            )
            return False

        if os.path.exists(real_target):
            try:
                shutil.rmtree(real_target)
                logger.info(
                    "local_k8s: deleted storage subPath %s for storage_id=%s",
                    real_target,
                    storage_id,
                )
            except OSError as e:
                logger.warning(
                    "local_k8s: failed to delete storage subPath %s: %s",
                    real_target,
                    e,
                )
                return False
        else:
            logger.info("local_k8s: storage subPath already gone: %s", real_target)
        return True


def local_k8s_plugin_factory(
    _credentials: ArcaCredentials | None = None,
    *,
    arca_utils: ArcaUtils | None = None,
) -> Callable[[ArcaCredentials], LocalK8sArcaSandboxPlugin]:
    """Return a callable that builds LocalK8sArcaSandboxPlugin with arca_utils baked in.

    The leading ``_credentials`` arg absorbs any positional argument that
    dependency_injector may pass when the provider is called with args.
    In normal flow, Singleton calls this with keyword args only, returning
    the inner ``_build`` function.
    """

    def _build(credentials: ArcaCredentials | None = None) -> LocalK8sArcaSandboxPlugin:
        return LocalK8sArcaSandboxPlugin(credentials=credentials, arca_utils=arca_utils)

    return _build
