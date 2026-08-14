"""Aliyun ACK-backed Arca sandbox plugin factory."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

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

from ._client_manager import AliyunAckClientManager
from ._config_type import AliyunAckPodConfig, AliyunAckTemplateConfig
from ._sandbox import AliyunAckSandbox

if TYPE_CHECKING:
    from kubernetes.client import CoreV1Api
    from kubernetes.client.rest import ApiException

logger = get_logger("plugin-sandbox-arca-aliyun-ack")

_BOLT_PORT = 20003


def _import_k8s() -> None:
    """Lazily bind Kubernetes SDK classes into this module namespace."""
    import sys

    _mod = sys.modules[__name__]
    if getattr(_mod, "_k8s_loaded", False):
        return
    from kubernetes.client import CoreV1Api as _CoreV1Api
    from kubernetes.client.rest import ApiException as _ApiException

    _mod.CoreV1Api = _CoreV1Api
    _mod.ApiException = _ApiException
    _mod._k8s_loaded = True


def _sanitize_pod_name(name: str) -> str:
    sanitized = "".join(c if c.isalnum() or c in "-." else "-" for c in name.lower())
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    return sanitized.strip("-.") or "aliyun-ack-sandbox"


def _wait_for_ready(
    core_api: Any, pod_name: str, namespace: str, ready_timeout_in_seconds: int
) -> None:
    deadline = time.monotonic() + ready_timeout_in_seconds
    while time.monotonic() < deadline:
        try:
            pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"failed to read pod {pod_name}: {e}") from e
        if pod.status and pod.status.phase == "Running":
            return
        time.sleep(1.0)
    raise RuntimeError(
        f"ACK pod {pod_name} did not become ready within {ready_timeout_in_seconds}s"
    )


def _resource_manifest(pod: AliyunAckPodConfig) -> dict[str, Any] | None:
    requests = {
        k: v for k, v in (("cpu", pod.cpu_request), ("memory", pod.memory_request)) if v
    }
    limits = {
        k: v for k, v in (("cpu", pod.cpu_limit), ("memory", pod.memory_limit)) if v
    }
    if not requests and not limits:
        return None
    manifest: dict[str, Any] = {}
    if requests:
        manifest["requests"] = requests
    if limits:
        manifest["limits"] = limits
    return manifest


class AliyunAckSandboxPlugin(ArcaSandboxPlugin):
    """Arca sandbox plugin backed by Aliyun ACK managed Kubernetes."""

    def __init__(
        self,
        config: ArcaCredentials | None = None,
        *,
        ack_templates: dict[str, AliyunAckTemplateConfig] | None = None,
        arca_utils: ArcaUtils | None = None,
    ) -> None:
        self._config = config
        self._ack_templates = ack_templates or {}
        self._arca_utils = arca_utils

    def _resolve_template(self, template_id: str | None) -> AliyunAckTemplateConfig:
        ack_id = template_id or (
            self._config.arca_template_id if self._config else None
        )
        if not ack_id:
            raise ValueError(
                "AliyunAckSandboxPlugin requires an arca_template_id "
                "(ALIYUN_ACK_TEMPLATE_xxx) to resolve the AliyunAckTemplate"
            )
        template = self._ack_templates.get(ack_id)
        if template is None:
            raise ValueError(
                f"No AliyunAckTemplate found for arca_template_id={ack_id!r}"
            )
        return template

    def _resolve_default_template(self) -> AliyunAckTemplateConfig:
        ack_id = self._config.arca_template_id if self._config else None
        if not ack_id:
            raise ValueError(
                "connect_sync_sandbox/delete_storage require an arca_template_id "
                "(ALIYUN_ACK_TEMPLATE_xxx) to resolve the AliyunAckTemplate"
            )
        template = self._ack_templates.get(ack_id)
        if template is None:
            raise ValueError(
                f"No AliyunAckTemplate found for arca_template_id={ack_id!r}"
            )
        return template

    def _client_for(self, template: AliyunAckTemplateConfig) -> Any:
        manager = AliyunAckClientManager(template.cluster)
        manager.validate()
        return manager.get_client()

    def _create_pod_and_service(
        self,
        pod_name: str,
        template: AliyunAckTemplateConfig,
        envs: dict[str, str] | None,
        metadata: dict[str, str] | None,
        ttl_in_minutes: int | None,
    ) -> None:
        _import_k8s()
        core_api = CoreV1Api(self._client_for(template))
        namespace = template.pod.namespace or "default"
        effective_envs = dict(template.pod.envs or {})
        if envs:
            effective_envs.update(envs)

        container: dict[str, Any] = {
            "name": "sandbox",
            "image": template.pod.image,
            "ports": [{"container_port": _BOLT_PORT}],
        }
        resources = _resource_manifest(template.pod)
        if resources:
            container["resources"] = resources
        if effective_envs:
            container["env"] = [
                {"name": k, "value": v} for k, v in effective_envs.items()
            ]

        labels = {
            "app": "avernet-arca-sandbox",
            "avernet.arcasandbox/template": template.template_id,
        }
        if metadata:
            labels.update(metadata)

        annotations: dict[str, str] = {
            "avernet.arcasandbox/image": template.pod.image,
        }
        if ttl_in_minutes is not None:
            annotations["avernet.arcasandbox/ttl-minutes"] = str(ttl_in_minutes)

        pod_spec: dict[str, Any] = {
            "containers": [container],
            "restart_policy": "Never",
        }
        if template.pod.service_account:
            pod_spec["service_account_name"] = template.pod.service_account

        pod = {
            "api_version": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": namespace,
                "labels": labels,
                "annotations": annotations,
            },
            "spec": pod_spec,
        }
        core_api.create_namespaced_pod(body=pod, namespace=namespace)

        service = {
            "api_version": "v1",
            "kind": "Service",
            "metadata": {"name": pod_name, "namespace": namespace, "labels": labels},
            "spec": {
                "selector": {"app": "avernet-arca-sandbox", "aliyun.ack.pod": pod_name},
                "ports": [{"port": _BOLT_PORT, "targetPort": _BOLT_PORT}],
            },
        }
        core_api.create_namespaced_service(body=service, namespace=namespace)

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
        _import_k8s()
        template = self._resolve_template(template_id)
        namespace = template.pod.namespace or "default"
        sandbox_id = f"aliyun-ack-{uuid.uuid4().hex[:12]}"
        pod_name = _sanitize_pod_name(sandbox_id)
        self._create_pod_and_service(
            pod_name=pod_name,
            template=template,
            envs=envs,
            metadata=metadata,
            ttl_in_minutes=ttl_in_minutes,
        )
        try:
            core_api = CoreV1Api(self._client_for(template))
            _wait_for_ready(core_api, pod_name, namespace, ready_timeout_in_seconds)
        except Exception:
            try:
                core_api = CoreV1Api(self._client_for(template))
                core_api.delete_namespaced_pod(name=pod_name, namespace=namespace)
            except Exception:
                pass
            raise

        device = AliyunAckSandbox(
            sandbox_id=sandbox_id,
            namespace=namespace,
            template_id=template.template_id,
            client=self._client_for(template),
            pod_name=pod_name,
            image=image or template.pod.image,
        )
        logger.info(
            "[aliyun_ack] sandbox created template=%s sandbox_id=%s pod=%s",
            template.template_id,
            sandbox_id,
            pod_name,
        )
        return device

    def connect_sync_sandbox(self, sandbox_id: str) -> ArcaSandbox:
        _import_k8s()
        pod_name = _sanitize_pod_name(sandbox_id)
        template = self._resolve_default_template()
        namespace = template.pod.namespace or "default"
        client = self._client_for(template)
        try:
            core_api = CoreV1Api(client)
            pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
        except ApiException as e:
            raise RuntimeError(
                f"Sandbox {sandbox_id} not found (pod {pod_name}) ({e.status})"
            ) from e

        template_id = "aliyun_ack"
        if pod.metadata and pod.metadata.labels:
            template_id = pod.metadata.labels.get(
                "avernet.arcasandbox/template", "aliyun_ack"
            )

        logger.info("[aliyun_ack] sandbox connected sandbox_id=%s", sandbox_id)
        return AliyunAckSandbox(
            sandbox_id=sandbox_id,
            namespace=namespace,
            template_id=template_id,
            client=client,
            pod_name=pod_name,
            image=template.pod.image,
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
        _import_k8s()
        template = self._resolve_default_template()
        namespace = template.pod.namespace or "default"
        pvc_name = _sanitize_pod_name(storage_id)
        logger.info(
            "[aliyun_ack] delete_storage storage_id=%s tenant_name=%s pvc=%s",
            storage_id,
            tenant_name,
            pvc_name,
        )
        try:
            core_api = CoreV1Api(self._client_for(template))
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
