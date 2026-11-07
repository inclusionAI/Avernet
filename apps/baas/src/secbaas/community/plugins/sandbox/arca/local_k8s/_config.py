"""Local Kubernetes platform configuration and parsing helpers."""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

from pydantic import ValidationError

from secbaas.community.api.device_manage import (
    OutBoundOperationRule,
    ResourceSpecification,
)

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
# to_thread 挪出事件循环之外的第二道防线）
_A2A_URL_DISCOVERY_TIMEOUT_S = 5.0

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
