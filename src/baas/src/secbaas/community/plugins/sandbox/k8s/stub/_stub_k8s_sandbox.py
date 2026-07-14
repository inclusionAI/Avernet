"""Mock K8s sandbox plugin — in-memory implementation for testing.

Provides:
- StubK8sSandboxPlugin: factory that creates/connects mock sandboxes
- StubK8sSandbox: K8sSandbox protocol mock implementation
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.api.device_manage import PodInfo
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.k8s import K8sSandbox, K8sSandboxPlugin

if TYPE_CHECKING:
    from secbaas.community.api.device_manage import K8sCredentials

logger = get_logger("plugin-sandbox-K8S")


class StubCommandResult:
    def __init__(self) -> None:
        self.exit_code = 0
        self.stdout = "mock-output"
        self.stderr = ""
        self.elapsed_time = 0.0


class StubK8sSandbox(K8sSandbox):
    """Mock implementation of K8sSandbox for testing."""

    def __init__(
        self,
        sandbox_id: str,
        namespace: str,
        pod_ip: str,
        pods: list[PodInfo] | None = None,
    ) -> None:
        self._sandbox_id = sandbox_id
        self._namespace = namespace
        self._pod_ip = pod_ip
        self._pods: list[PodInfo] = pods or [
            PodInfo(name="bot", ready=True, restart_count=0, state="running")
        ]
        self._scaling_in_progress = False
        self._destroyed = False

    @property
    def is_ready(self) -> bool:
        return True

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id

    def get_info(self) -> Any:
        logger.info("[stub] get_info sandbox_id=%s", self._sandbox_id)
        status = "TERMINATING" if self._destroyed else "RUNNING"
        if self._destroyed:
            return {
                "sandbox_id": self._sandbox_id,
                "status": status,
                "replicas": 0,
                "available_replicas": 0,
                "pod_ip": self._pod_ip,
                "namespace": self._namespace,
                "conditions": [{"type": "Ready", "status": "False"}],
                "container_statuses": [],
            }
        # Build container_statuses from PodInfo list
        container_statuses = [
            {
                "name": p.name,
                "ready": p.ready,
                "restart_count": p.restart_count,
                "state": p.state,
                "image": p.image,
            }
            for p in self._pods
        ]
        # Determine conditions: Ready=False if any pod is not ready or not running
        any_unhealthy = any(not p.ready or p.state != "running" for p in self._pods)
        conditions = [{"type": "Ready", "status": "False"}] if any_unhealthy else []
        return {
            "sandbox_id": self._sandbox_id,
            "status": status,
            "replicas": 0 if self._destroyed else 1,
            "available_replicas": 0 if self._destroyed else 1,
            "pod_ip": self._pod_ip,
            "namespace": self._namespace,
            "conditions": conditions,
            "container_statuses": container_statuses,
        }

    def exec_command(
        self,
        cmd: str,
        timeout_in_millis: int = 30000,
        envs: dict[str, str] | None = None,
    ) -> Any:
        logger.info(
            "[stub] exec_command sandbox_id=%s timeout=%d cmd=%s",
            self._sandbox_id,
            timeout_in_millis,
            cmd[:200],
        )
        return StubCommandResult()

    def destroy(self) -> Any:
        self._destroyed = True
        logger.info("[stub] sandbox destroyed sandbox_id=%s", self._sandbox_id)
        return True

    def restart(self) -> Any:
        if self._destroyed:
            raise RuntimeError("Sandbox already destroyed (404)")
        logger.info("[stub] sandbox restarted sandbox_id=%s", self._sandbox_id)
        return True

    def update(self, **kwargs: Any) -> Any:
        if self._scaling_in_progress:
            raise RuntimeError("Conflict (409): concurrent scale operation in progress")
        self._scaling_in_progress = True
        try:
            logger.info(
                "[stub] sandbox updated sandbox_id=%s kwargs=%s",
                self._sandbox_id,
                kwargs,
            )
            return True
        finally:
            self._scaling_in_progress = False


class StubK8sSandboxPlugin(K8sSandboxPlugin):
    """Mock K8s sandbox plugin for testing — no real K8s SDK calls."""

    def __init__(
        self,
        credentials: K8sCredentials | None = None,
        default_pods: list[PodInfo] | None = None,
    ) -> None:
        self._sandboxes: dict[str, StubK8sSandbox] = {}
        self._next_ip_index: int = 1
        self._credentials: K8sCredentials | None = credentials
        self._default_pods: list[PodInfo] = default_pods or [
            PodInfo(name="bot", ready=True, restart_count=0, state="running")
        ]
        self._configmaps: dict[str, str] = {}
        logger.info("[stub] initialized")

    def create_device(
        self,
        template_id: int,
        template_uuid: str,
        tenant_name: str,
        namespace: str,
        image: str,
        cpu_request: str,
        cpu_limit: str,
        memory_request: str,
        memory_limit: str,
        envs: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
        timeout_in_millis: int = 120000,
        envoy_yaml: str | None = None,
    ) -> StubK8sSandbox:
        sandbox_id = f"stub-K8S-{uuid.uuid4().hex[:12]}"
        pod_ip = f"10.244.0.{min(self._next_ip_index, 254)}"
        self._next_ip_index = min(self._next_ip_index + 1, 255)
        device = StubK8sSandbox(sandbox_id, namespace, pod_ip, pods=self._default_pods)
        self._sandboxes[sandbox_id] = device
        if envoy_yaml:
            statefulset_name = (
                metadata.get("app.kubernetes.io/part-of", tenant_name)
                if metadata
                else tenant_name
            )
            cm_name = f"{statefulset_name}-proxy-rules"
            self._configmaps[cm_name] = envoy_yaml
            logger.info(
                "[stub] sandbox created with sidecar: %s proxy ConfigMap %s",
                sandbox_id,
                cm_name,
            )
        else:
            logger.info(
                "[stub] sandbox created template_id=%s sandbox_id=%s pod_ip=%s",
                template_id,
                sandbox_id,
                pod_ip,
            )
        return device

    def connect_device(
        self,
        sandbox_id: str,
        namespace: str,
    ) -> StubK8sSandbox:
        if sandbox_id not in self._sandboxes:
            raise RuntimeError("Deployment not found (404)")
        if self._sandboxes[sandbox_id]._namespace != namespace:
            raise RuntimeError("Deployment not found (404)")
        logger.info(
            "[stub] sandbox connected sandbox_id=%s namespace=%s", sandbox_id, namespace
        )
        return self._sandboxes[sandbox_id]

    def list_instances(
        self,
        namespace: str,
        label_selector: str | None = None,
    ) -> list[Any]:
        logger.info(
            "[stub] list_instances namespace=%s label_selector=%s",
            namespace,
            label_selector,
        )
        return [
            {
                "sandbox_id": s._sandbox_id,
                "status": "RUNNING",
                "namespace": s._namespace,
                "pod_ip": s._pod_ip,
            }
            for s in self._sandboxes.values()
            if s._namespace == namespace
        ]

    def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        namespace: str,
    ) -> WsConnectionInfo:
        normalized_path = "/" + path.lstrip("/")
        logger.info(
            "[stub] resolve_ws_conn_info device_id=%s port=%d path=%s namespace=%s",
            paas_device_id,
            port,
            normalized_path,
            namespace,
        )
        return WsConnectionInfo(
            ws_url=f"ws://localhost:{port}{normalized_path}",
            token="",
            target=f"K8S_{paas_device_id}:{port}",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )

    def resolve_invoke_http_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        namespace: str,
    ) -> HttpConnectionInfo:
        normalized_path = "/" + path.lstrip("/")
        logger.info(
            "[stub] resolve_invoke_http_info device_id=%s port=%d path=%s namespace=%s",
            paas_device_id,
            port,
            normalized_path,
            namespace,
        )
        return HttpConnectionInfo(
            http_url=f"http://localhost:{port}{normalized_path}",
            token="",
        )

    def invoke_http_in_device(
        self,
        paas_device_id: str,
        method: str,
        port: int,
        path: str,
        namespace: str,
        query_string: str | None = None,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        logger.info(
            "[stub] invoke_http_in_device device_id=%s method=%s port=%d path=%s",
            paas_device_id,
            method,
            port,
            path,
        )
        return {
            "status_code": 200,
            "headers": {"Content-Type": "text/plain"},
            "body": "bW9jayBodHRwIHJlc3BvbnNl",
        }

    def close(self) -> None:
        logger.info("[stub] plugin closed")
        self._sandboxes.clear()
        self._next_ip_index = 1
        self._configmaps.clear()
        logger.info("[stub] configmaps cleared")

    def update_outbound_operation_rule(
        self,
        statefulset_name: str,
        namespace: str,
        envoy_yaml: str,
    ) -> None:
        """Update the outbound proxy rules ConfigMap for a StatefulSet.

        Creates the ConfigMap if it does not exist (first call).
        Patches the existing ConfigMap on subsequent calls.
        Envoy hot-reloads via ConfigMap volume mount symlink rotation.

        Args:
            statefulset_name: K8s StatefulSet name.
            namespace: K8s namespace.
            envoy_yaml: Complete Envoy configuration YAML string.

        Raises:
            RuntimeError: On API failure (404, 409, etc.).
        """
        cm_name = f"{statefulset_name}-proxy-rules"
        self._configmaps[cm_name] = envoy_yaml
        logger.info(
            "[stub] update_outbound_operation_rule configmap=%s namespace=%s",
            cm_name,
            namespace,
        )
