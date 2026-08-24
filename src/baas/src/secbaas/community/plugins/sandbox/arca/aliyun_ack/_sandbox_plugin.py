"""Aliyun ACK-backed Arca sandbox plugin factory."""

from __future__ import annotations

import string
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from kubernetes.client import CoreV1Api
from kubernetes.client.rest import ApiException
from kubernetes.utils.create_from_yaml import create_from_yaml

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import (
    ArcaCredentials,
    MountPoint,
    OutBoundOperationRule,
    ResourceSpecification,
    Storage,
)
from secbaas.community.logger import get_logger
from secbaas.community.plugins.sandbox.utils.arca_utils import ArcaUtils
from secbaas.community.spi.sandbox.arca import ArcaSandbox, ArcaSandboxPlugin

from ._client_manager import AliyunAckClientManager, AliyunAckClusterConfig
from ._sandbox import AliyunAckSandbox

logger = get_logger("plugin-sandbox")

_BOLT_PORT = 20003
_TEMPLATE_DIR = Path(__file__).parent / "template"


def _sanitize_pod_name(name: str) -> str:
    sanitized = "".join(c if c.isalnum() or c in "-." else "-" for c in name.lower())
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    return sanitized.strip("-.") or "aliyun-ack-sandbox"


def _wait_for_ready(
    core_api: Any, uid: str, namespace: str, ready_timeout_in_seconds: int
) -> str:
    """Poll the Pod created by the Deployment until Running.

    Uses the ``biz-id`` label to find the Pod, since Deployment Pod names
    include a random suffix. Returns the resolved Pod name.
    """
    label_selector = f"biz-id={uid}"
    deadline = time.monotonic() + ready_timeout_in_seconds
    while time.monotonic() < deadline:
        try:
            pods = core_api.list_namespaced_pod(
                namespace=namespace, label_selector=label_selector
            )
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"failed to list pods for uid={uid}: {e}") from e
        if pods.items:
            pod = pods.items[0]
            if pod.status and pod.status.phase == "Running":
                return pod.metadata.name
        time.sleep(1.0)
    raise RuntimeError(
        f"ACK pod for uid={uid} did not become ready within {ready_timeout_in_seconds}s"
    )


def _render_template(template_id: str, variables: dict[str, str]) -> str:
    """Read ``template/{template_id}.yaml`` and substitute ``${VAR}`` placeholders."""
    template_path = _TEMPLATE_DIR / f"{template_id}.yaml"
    raw = template_path.read_text(encoding="utf-8")
    return string.Template(raw).safe_substitute(variables)


def _build_template_vars(
    uid: str,
    namespace: str,
    images: dict[str, str] | None,
    storage: Storage | None = None,
    resource_spec: ResourceSpecification | None = None,
) -> dict[str, str]:
    """Build runtime variables for template rendering.

    ``images`` is a mapping of container name → image address (e.g.
    ``{"openclaw": "...", "api-key-proxy": "...", "init": "..."}``).
    Each is exposed as ``${OPENCLAW_IMAGE}``, ``${API_KEY_PROXY_IMAGE}``,
    ``${INIT_IMAGE}`` respectively.
    """
    images = images or {}
    storage_id = (
        _sanitize_pod_name(storage.storage_id)
        if storage and storage.storage_id
        else uid
    )
    mount_path = storage.path if storage and storage.path else "/home/admin"
    storage_size = storage.quota if storage and storage.quota else "1Gi"
    cpu = str(resource_spec.cpu) if resource_spec else "1"
    memory = f"{resource_spec.memory}Gi" if resource_spec else "1Gi"
    return {
        "UID": uid,
        "NAMESPACE": namespace,
        "OPENCLAW_IMAGE": images.get("openclaw", "openclaw:latest"),
        "ENVOY_SIDECAR_IMAGE": images.get(
            "envoy-sidecar", "envoyproxy/envoy:v1.30-latest"
        ),
        "INIT_IMAGE": images.get("init", "busybox:latest"),
        "NAS_SERVER": images.get("nas-server", ""),
        "STORAGE_ID": storage_id,
        "STORAGE_SIZE": storage_size,
        "MOUNT_PATH": mount_path,
        "CPU_REQUEST": cpu,
        "CPU_LIMIT": cpu,
        "MEMORY_REQUEST": memory,
        "MEMORY_LIMIT": memory,
    }


def _convert_outbound_rules(rule: OutBoundOperationRule | None) -> str:
    """Convert OutBoundOperationRule to envoy sidecar header-rules.yaml format.

    Groups header_operation_rules by their domains, then maps each action
    (replace/set → set, remove → remove) into the header-rules YAML structure.
    Returns the YAML content (indented for embedding inside a ConfigMap data
    block, i.e. 2-space indented).
    """
    if not rule or not rule.header_operation_rules:
        return "  rules: []"

    # Group rules by domain tuple so rules sharing the same domains
    # merge into one outbound rule entry.
    domain_groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for h in rule.header_operation_rules:
        key = tuple(sorted(h.domains))
        if key not in domain_groups:
            domain_groups[key] = {
                "name": h.domains[0],
                "domains": list(h.domains),
                "set": [],
                "remove": [],
            }
        action = (h.action or "").lower()
        if action in ("replace", "set"):
            domain_groups[key]["set"].append(
                {"header": h.header_name, "value": h.value}
            )
        elif action == "remove":
            domain_groups[key]["remove"].append(h.header_name)

    rules_data = {"rules": list(domain_groups.values())}
    header_yaml = yaml.safe_dump(rules_data, default_flow_style=False, sort_keys=False)
    # Indent each line by 2 spaces for ConfigMap data block embedding.
    return "\n".join(f"  {line}" for line in header_yaml.strip().splitlines())


class AliyunAckSandboxPlugin(ArcaSandboxPlugin):
    """Arca sandbox plugin backed by Aliyun ACK managed Kubernetes."""

    def __init__(
        self,
        config: ArcaCredentials | None = None,
        *,
        api_server: str = "",
        token: str = "",
        namespace: str = "default",
        default_images: dict[str, str] | None = None,
        arca_utils: ArcaUtils | None = None,
    ) -> None:
        self._config = config
        self._api_server = api_server
        self._token = token
        self._namespace = namespace
        self._default_images = default_images or {}
        self._arca_utils = arca_utils
        self._client_manager: AliyunAckClientManager | None = None

    def _get_namespace(self) -> str:
        return self._namespace

    def _get_template_id(self) -> str:
        ack_id = self._config.arca_template_id if self._config else None
        if not ack_id:
            raise ValueError(
                "AliyunAckSandboxPlugin requires an arca_template_id "
                "to locate the YAML template file"
            )
        return ack_id

    def _client(self) -> Any:
        if self._client_manager is None:
            self._client_manager = AliyunAckClientManager(
                AliyunAckClusterConfig(self._api_server, self._token, self._namespace)
            )
            self._client_manager.validate()
        return self._client_manager.get_client()

    def _create_deployment(
        self,
        uid: str,
        template_id: str,
        namespace: str,
        storage: Storage | None,
        resource_spec: ResourceSpecification | None,
        outbound_operation_rule: OutBoundOperationRule | None = None,
    ) -> tuple[str, str]:
        """Render the template YAML and apply it to the cluster.

        Returns ``(deployment_name, container_name)`` extracted from the
        rendered template so callers need not hard-code them.
        """
        images = self._default_images.get(template_id, {})
        variables = _build_template_vars(
            uid,
            namespace,
            images,
            storage=storage,
            resource_spec=resource_spec,
        )
        variables["HEADER_RULES_YAML"] = _convert_outbound_rules(
            outbound_operation_rule
        )
        rendered = _render_template(template_id, variables)

        docs = list(yaml.safe_load_all(rendered))
        deployment_name = ""
        container_name = ""
        for doc in docs:
            if not doc:
                continue
            if doc.get("kind") == "Deployment":
                deployment_name = doc["metadata"]["name"]
                containers = doc["spec"]["template"]["spec"]["containers"]
                container_name = containers[0]["name"]
                break

        client = self._client()
        create_from_yaml(
            k8s_client=client,
            yaml_objects=docs,
            namespace=namespace,
        )

        logger.info(
            "[aliyun_ack] template applied uid=%s namespace=%s deployment=%s",
            uid,
            namespace,
            deployment_name,
        )
        return deployment_name, container_name

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
        namespace = self._get_namespace()
        sandbox_id = f"{template_id}-{uuid.uuid4().hex[:12]}"
        uid = _sanitize_pod_name(sandbox_id)
        deployment_name, container_name = self._create_deployment(
            uid, template_id, namespace, storage, resource_spec,
            outbound_operation_rule=outbound_operation_rule,
        )
        try:
            core_api = CoreV1Api(self._client())
            pod_name = _wait_for_ready(
                core_api, uid, namespace, ready_timeout_in_seconds
            )
        except Exception:
            try:
                core_api = CoreV1Api(self._client())
                core_api.delete_namespaced_deployment(
                    name=deployment_name, namespace=namespace
                )
            except Exception:
                pass
            raise

        device = AliyunAckSandbox(
            sandbox_id=sandbox_id,
            namespace=namespace,
            template_id=template_id,
            client=self._client(),
            pod_name=pod_name,
            deployment_name=deployment_name,
            container_name=container_name,
            image=image or "",
        )
        logger.info(
            "[aliyun_ack] sandbox created template=%s sandbox_id=%s pod=%s",
            template_id,
            sandbox_id,
            pod_name,
        )
        return device

    def connect_sync_sandbox(self, sandbox_id: str) -> ArcaSandbox:
        uid = _sanitize_pod_name(sandbox_id)
        namespace = self._get_namespace()
        client = self._client()
        try:
            core_api = CoreV1Api(client)
            pods = core_api.list_namespaced_pod(
                namespace=namespace, label_selector=f"biz-id={uid}"
            )
            if not pods.items:
                raise RuntimeError(
                    f"Sandbox {sandbox_id} not found (no pod for biz-id={uid})"
                )
            pod = pods.items[0]
            pod_name = pod.metadata.name
        except ApiException as e:
            raise RuntimeError(f"Sandbox {sandbox_id} not found ({e.status})") from e

        template_id = "aliyun_ack"
        if pod.metadata and pod.metadata.labels:
            template_id = pod.metadata.labels.get(
                "avernet.arcasandbox/template", "aliyun_ack"
            )

        deployment_name = ""
        if pod.metadata and pod.metadata.owner_references:
            for ref in pod.metadata.owner_references:
                if ref.kind == "ReplicaSet":
                    deployment_name = ref.name.rsplit("-", 1)[0]
                    break

        container_name = ""
        if pod.spec and pod.spec.containers:
            container_name = pod.spec.containers[0].name

        logger.info("[aliyun_ack] sandbox connected sandbox_id=%s", sandbox_id)
        return AliyunAckSandbox(
            sandbox_id=sandbox_id,
            namespace=namespace,
            template_id=template_id,
            client=client,
            pod_name=pod_name,
            deployment_name=deployment_name,
            container_name=container_name,
            image="",
        )

    def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        template_id: int | None = None,
    ) -> WsConnectionInfo:
        norm_path = path if path.startswith("/") else f"/{path}"
        target = self._arca_utils._get_arca_target(
            paas_device_id, port=port, template_id=template_id
        )
        token = self._arca_utils._get_proxypass_token(
            paas_device_id, port=port, template_id=template_id, ttl=120
        )
        url = self._arca_utils.build_proxypass_url(target, norm_path, scheme="wss")
        return WsConnectionInfo(
            ws_url=url,
            token=token,
            target=target,
            expires_at=datetime.now(UTC) + timedelta(seconds=120),
        )

    def resolve_http_connection_info(
        self,
        paas_device_id: str,
        port: int,
        path: str = "/",
        template_id: int | None = None,
    ) -> HttpConnectionInfo:
        norm_path = path if path.startswith("/") else f"/{path}"
        target = self._arca_utils._get_arca_target(
            paas_device_id, port=port, template_id=template_id
        )
        token = self._arca_utils._get_proxypass_token(
            paas_device_id, port=port, template_id=template_id
        )
        url = self._arca_utils.build_proxypass_url(target, norm_path, scheme="https")
        return HttpConnectionInfo(http_url=url, token=token, target=target)

    def close(self) -> None:
        """Release any ACK clients."""

    def delete_storage(self, storage_id: str, tenant_name: str) -> bool:
        namespace = self._get_namespace()
        pvc_name = _sanitize_pod_name(storage_id)
        logger.info(
            "[aliyun_ack] delete_storage storage_id=%s tenant_name=%s pvc=%s",
            storage_id,
            tenant_name,
            pvc_name,
        )
        try:
            core_api = CoreV1Api(self._client())
            core_api.delete_namespaced_persistent_volume_claim(
                name=pvc_name, namespace=namespace
            )
            return True
        except ApiException as e:
            if e.status == 404:
                logger.info(
                    "[aliyun_ack] delete_storage: pvc %s not found (404), idempotent",
                    pvc_name,
                )
                return True
            logger.warning("[aliyun_ack] delete_storage failed (%s)", e.status)
            return False


def aliyun_ack_plugin_factory(
    _credentials=None,
    *,
    api_server: str = "",
    token: str = "",
    namespace: str = "default",
    default_images: dict[str, str] | None = None,
    arca_utils: ArcaUtils | None = None,
):
    """Return a callable that builds AliyunAckSandboxPlugin with config baked in.

    The leading ``_credentials`` arg absorbs any positional argument that
    dependency_injector may pass when the provider is called with args.
    In normal flow, Singleton calls this with keyword args only, returning
    the inner ``_build`` function.
    """

    def _build(credentials=None):
        return AliyunAckSandboxPlugin(
            config=credentials,
            api_server=api_server,
            token=token,
            namespace=namespace,
            default_images=default_images,
            arca_utils=arca_utils,
        )

    return _build
