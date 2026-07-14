"""Real K8s sandbox plugin — production kubernetes SDK implementation.

Provides:
- RealK8sSandbox: wraps kubernetes.client.V1Pod, implements K8sSandbox Protocol
- RealK8sSandboxPlugin: wraps K8sClientManager + K8sCredentials, implements K8sSandboxPlugin Protocol
"""

from __future__ import annotations

import base64
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.k8s import K8sSandbox, K8sSandboxPlugin

if TYPE_CHECKING:
    from kubernetes.client import (
        ApiClient,
        AppsV1Api,
        CoreV1Api,
        V1ConfigMap,
        V1ConfigMapVolumeSource,
        V1Container,
        V1ContainerPort,
        V1DeleteOptions,
        V1EnvVar,
        V1LabelSelector,
        V1ObjectMeta,
        V1Pod,
        V1PodSpec,
        V1PodTemplateSpec,
        V1ResourceRequirements,
        V1StatefulSet,
        V1StatefulSetSpec,
        V1Volume,
        V1VolumeMount,
    )
    from kubernetes.client.rest import ApiException

    from secbaas.community.api.device_manage import K8sCredentials
    from secbaas.community.plugins.sandbox.k8s.real import K8sClientManager

logger = get_logger("plugin-sandbox-k8s-real")

_RFC1123_PATTERN = re.compile(r"[^a-z0-9-]")
_MULTI_DASH_PATTERN = re.compile(r"-+")


def _import_k8s() -> None:
    """Lazy-import Kubernetes SDK classes into the module namespace.

    Called once at the top of each method that uses kubernetes classes.
    After the first call, subsequent calls are no-ops.
    """
    _mod = sys.modules[__name__]
    if getattr(_mod, "_k8s_loaded", False):
        return

    from kubernetes.client import (  # noqa: TC003
        AppsV1Api as _AppsV1Api,
    )
    from kubernetes.client import (
        CoreV1Api as _CoreV1Api,
    )
    from kubernetes.client import (
        V1ConfigMap as _V1ConfigMap,
    )
    from kubernetes.client import (
        V1ConfigMapVolumeSource as _V1ConfigMapVolumeSource,
    )
    from kubernetes.client import (
        V1Container as _V1Container,
    )
    from kubernetes.client import (
        V1ContainerPort as _V1ContainerPort,
    )
    from kubernetes.client import (
        V1DeleteOptions as _V1DeleteOptions,
    )
    from kubernetes.client import (
        V1EnvVar as _V1EnvVar,
    )
    from kubernetes.client import (
        V1LabelSelector as _V1LabelSelector,
    )
    from kubernetes.client import (
        V1ObjectMeta as _V1ObjectMeta,
    )
    from kubernetes.client import (
        V1Pod as _V1Pod,
    )
    from kubernetes.client import (
        V1PodSpec as _V1PodSpec,
    )
    from kubernetes.client import (
        V1PodTemplateSpec as _V1PodTemplateSpec,
    )
    from kubernetes.client import (
        V1ResourceRequirements as _V1ResourceRequirements,
    )
    from kubernetes.client import (
        V1StatefulSet as _V1StatefulSet,
    )
    from kubernetes.client import (
        V1StatefulSetSpec as _V1StatefulSetSpec,
    )
    from kubernetes.client import (
        V1Volume as _V1Volume,
    )
    from kubernetes.client import (
        V1VolumeMount as _V1VolumeMount,
    )
    from kubernetes.client.rest import ApiException as _ApiException

    _mod.AppsV1Api = _AppsV1Api
    _mod.CoreV1Api = _CoreV1Api
    _mod.V1ConfigMap = _V1ConfigMap
    _mod.V1ConfigMapVolumeSource = _V1ConfigMapVolumeSource
    _mod.V1Container = _V1Container
    _mod.V1ContainerPort = _V1ContainerPort
    _mod.V1DeleteOptions = _V1DeleteOptions
    _mod.V1EnvVar = _V1EnvVar
    _mod.V1LabelSelector = _V1LabelSelector
    _mod.V1ObjectMeta = _V1ObjectMeta
    _mod.V1Pod = _V1Pod
    _mod.V1PodSpec = _V1PodSpec
    _mod.V1PodTemplateSpec = _V1PodTemplateSpec
    _mod.V1ResourceRequirements = _V1ResourceRequirements
    _mod.V1StatefulSet = _V1StatefulSet
    _mod.V1StatefulSetSpec = _V1StatefulSetSpec
    _mod.V1Volume = _V1Volume
    _mod.V1VolumeMount = _V1VolumeMount
    _mod.ApiException = _ApiException
    _mod._k8s_loaded = True


def _sanitize_rfc1123(name: str) -> str:
    """Sanitize a name to RFC 1123 subdomain format.

    Converts to lowercase, replaces non-alphanumeric characters with hyphens,
    collapses multiple hyphens, and strips leading/trailing hyphens.
    Returns "k8s-bot" if the result is empty.
    """
    sanitized = _RFC1123_PATTERN.sub("-", name.lower())
    sanitized = _MULTI_DASH_PATTERN.sub("-", sanitized)
    sanitized = sanitized.strip("-")
    return sanitized if sanitized else "k8s-bot"


def _env_list_from_dict(envs: dict[str, str] | None) -> list[Any]:
    """Convert env dict to list of V1EnvVar-compatible dicts."""
    if not envs:
        return []
    _import_k8s()
    return [V1EnvVar(name=k, value=v) for k, v in envs.items()]


class RealK8sSandbox(K8sSandbox):
    """K8s sandbox wrapping a kubernetes.client.V1Pod.

    Implements the K8sSandbox Protocol (5 methods) and provides
    is_ready / sandbox_id properties. All K8s API calls go through
    the shared ApiClient.
    """

    def __init__(
        self, sandbox_id: str, namespace: str, pod: V1Pod | None, client: ApiClient
    ) -> None:
        self._sandbox_id = sandbox_id
        self._namespace = namespace
        self._pod = pod
        self._client = client

    @property
    def is_ready(self) -> bool:
        """Check if the Pod is in Running phase."""
        if self._pod is None:
            return False
        if self._pod.status:
            return self._pod.status.phase == "Running"
        return False

    @property
    def sandbox_id(self) -> str:
        """Return the sandbox Pod name."""
        return self._sandbox_id

    def get_info(self) -> dict[str, Any]:
        """Extract Pod status information into a dict.

        Returns a dict with sandbox_id, namespace, status (phase), pod_ip,
        container_statuses, and conditions. When pod is None (newly created
        or lazy-loaded), returns a minimal dict with status "PROVISIONING".
        """
        logger.info("[real] get_info sandbox_id=%s", self._sandbox_id)
        if self._pod is None:
            return {
                "sandbox_id": self._sandbox_id,
                "namespace": self._namespace,
                "status": "PROVISIONING",
                "pod_ip": None,
                "container_statuses": [],
                "conditions": [],
            }
        status = self._pod.status
        phase = getattr(status, "phase", None) or "Unknown"
        pod_ip = getattr(status, "pod_ip", None)
        container_statuses_raw = getattr(status, "container_statuses", None) or []
        conditions_raw = getattr(status, "conditions", None) or []

        container_statuses = []
        for cs in container_statuses_raw:
            state_info = {}
            if cs.state:
                for state_name in ("running", "waiting", "terminated"):
                    state_obj = getattr(cs.state, state_name, None)
                    if state_obj is not None:
                        state_info = {
                            "type": state_name,
                            "reason": getattr(state_obj, "reason", None),
                            "message": getattr(state_obj, "message", None),
                        }
                        break
                if not state_info:
                    state_info = {"type": "unknown"}
            else:
                state_info = {"type": "unknown"}
            container_statuses.append(
                {
                    "name": cs.name,
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "state": state_info.get("type", "unknown"),
                    "image": cs.image,
                }
            )

        conditions = []
        for cond in conditions_raw:
            conditions.append(
                {
                    "type": cond.type,
                    "status": cond.status,
                    "reason": getattr(cond, "reason", None),
                }
            )

        return {
            "sandbox_id": self._sandbox_id,
            "namespace": self._namespace,
            "status": phase,
            "pod_ip": pod_ip,
            "container_statuses": container_statuses,
            "conditions": conditions,
        }

    def exec_command(
        self,
        cmd: str,
        timeout_in_millis: int = 30000,
        envs: dict[str, str] | None = None,
    ) -> Any:
        """Execute a command inside the Pod via exec.

        Args:
            cmd: Command string to execute.
            timeout_in_millis: Maximum execution time in milliseconds.
            envs: Environment variables (appended to Pod env for exec).

        Returns:
            A CommandResult-like object with exit_code, stdout, stderr, elapsed_time.
        """
        logger.info(
            "[real] exec_command sandbox_id=%s timeout=%d cmd=%s",
            self._sandbox_id,
            timeout_in_millis,
            cmd[:200],
        )
        _import_k8s()
        try:
            core_api = CoreV1Api(self._client)
            resp = core_api.connect_get_namespaced_pod_exec(
                name=self._sandbox_id,
                namespace=self._namespace,
                command=["/bin/sh", "-c", cmd],
                stderr=True,
                stdout=True,
                stdin=False,
                tty=False,
                _preload_content=False,
            )
            # The exec response is a WSClient; read the output
            resp.run_forever(timeout=timeout_in_millis / 1000.0)
            exit_code = resp.returncode if resp.returncode is not None else 0
            stdout = resp.read_stdout() or ""
            stderr = resp.read_stderr() or ""
            elapsed = 0.0  # SDK exec doesn't expose timing

            # Return a simple namespace object matching CommandResult convention
            class _ExecResult:
                pass

            result = _ExecResult()
            result.exit_code = exit_code
            result.stdout = stdout
            result.stderr = stderr
            result.elapsed_time = elapsed
            return result
        except ApiException as e:
            raise RuntimeError(f"exec_command failed ({e.status})") from e

    def destroy(self) -> bool:
        """Delete the StatefulSet that owns this sandbox Pod.

        Extracts the StatefulSet name from sandbox_id ("{name}-{ordinal}").
        Uses Foreground propagation to ensure Pods are cleaned up first.
        Idempotent: returns True if the StatefulSet is already gone (404).

        Also attempts to clean up the proxy-rules ConfigMap. ConfigMap
        cleanup failure is non-fatal (logged as warning).
        """
        logger.info("[real] destroy sandbox_id=%s", self._sandbox_id)
        # Parse StatefulSet name from Pod name: remove trailing ordinal
        parts = self._sandbox_id.rsplit("-", 1)
        statefulset_name = parts[0] if len(parts) == 2 else self._sandbox_id

        _import_k8s()
        try:
            apps_api = AppsV1Api(self._client)
            apps_api.delete_namespaced_stateful_set(
                name=statefulset_name,
                namespace=self._namespace,
                body=V1DeleteOptions(propagation_policy="Foreground"),
            )
        except ApiException as e:
            if e.status == 404:
                logger.info(
                    "[real] destroy: StatefulSet %s already gone (404), idempotent",
                    statefulset_name,
                )
            else:
                raise RuntimeError(f"destroy failed ({e.status})") from e

        # Clean up proxy-rules ConfigMap (non-fatal)
        cm_name = f"{statefulset_name}-proxy-rules"
        try:
            core_api = CoreV1Api(self._client)
            core_api.delete_namespaced_config_map(
                name=cm_name, namespace=self._namespace
            )
            logger.info("[real] destroy: ConfigMap %s deleted", cm_name)
        except ApiException as e:
            if e.status == 404:
                pass
            else:
                logger.warning(
                    "[real] destroy: failed to delete ConfigMap %s: (%s)",
                    cm_name,
                    e.status,
                )

        return True

    def restart(self) -> bool:
        """Restart the sandbox Pod by deleting it.

        Deleting the Pod triggers the StatefulSet controller to recreate
        it with the same name, effectively restarting the container.
        Uses Foreground deletion to ensure graceful termination.
        Idempotent: returns True if the Pod is already gone (404).
        """
        logger.info("[real] restart sandbox_id=%s", self._sandbox_id)
        _import_k8s()
        try:
            core_api = CoreV1Api(self._client)
            core_api.delete_namespaced_pod(
                name=self._sandbox_id,
                namespace=self._namespace,
                body=V1DeleteOptions(propagation_policy="Foreground"),
            )
            return True
        except ApiException as e:
            if e.status == 404:
                logger.info(
                    "[real] restart: Pod %s already gone (404), idempotent",
                    self._sandbox_id,
                )
                return True
            raise RuntimeError(f"restart failed ({e.status})") from e

    def update(self, **kwargs: Any) -> bool:
        """Update the StatefulSet spec via strategic merge patch.

        Accepts kwargs that map to StatefulSet spec fields (e.g., replicas,
        template.spec.containers[0].resources, template.spec.containers[0].image).
        """
        logger.info(
            "[real] update sandbox_id=%s kwargs=%s",
            self._sandbox_id,
            kwargs,
        )
        _import_k8s()
        try:
            parts = self._sandbox_id.rsplit("-", 1)
            statefulset_name = parts[0] if len(parts) == 2 else self._sandbox_id

            apps_api = AppsV1Api(self._client)
            apps_api.patch_namespaced_stateful_set(
                name=statefulset_name,
                namespace=self._namespace,
                body={"spec": kwargs},
            )
            return True
        except ApiException as e:
            raise RuntimeError(f"update failed ({e.status})") from e


class RealK8sSandboxPlugin(K8sSandboxPlugin):
    """Real Kubernetes sandbox plugin using kubernetes Python SDK.

    Implements the K8sSandboxPlugin Protocol (8 methods). Wraps
    K8sClientManager for ApiClient lifecycle and K8sCredentials for
    cluster configuration. All methods are synchronous (def).
    """

    def __init__(
        self, credentials: K8sCredentials, client_manager: K8sClientManager
    ) -> None:
        self._credentials = credentials
        self._client_manager = client_manager
        self._client: ApiClient | None = None
        logger.info("[real] plugin initialized")

    def _ensure_client(self) -> ApiClient:
        """Lazily get or create the shared ApiClient from K8sClientManager."""
        _import_k8s()
        if self._client is None:
            self._client = self._client_manager.get_or_create_client(self._credentials)
        return self._client

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
    ) -> RealK8sSandbox:
        """Create a K8s sandbox via StatefulSet.

        First call creates a new StatefulSet with replicas=1. Subsequent
        calls for the same tenant_name+template_uuid scale replicas += 1.
        Returns RealK8sSandbox wrapping the newly created Pod.

        When envoy_yaml is non-empty, an Envoy sidecar container is injected
        alongside a proxy ConfigMap volume, and HTTP_PROXY/HTTPS_PROXY env
        vars are set on the bot-runtime container so outbound HTTP traffic
        routes through the sidecar.
        """
        statefulset_name = _sanitize_rfc1123(f"{tenant_name}-{template_uuid}")
        service_name = f"{statefulset_name}-svc"
        labels = metadata.copy() if metadata else {}
        labels.update(
            {
                "app.kubernetes.io/managed-by": "secbaas",
                "app.kubernetes.io/part-of": "secbaas",
                "app.kubernetes.io/component": "bot-runtime",
                "tenant": tenant_name,
                "template-uuid": template_uuid,
            }
        )

        sidecar_active = bool(envoy_yaml)
        logger.info(
            "[real] create_device statefulset=%s namespace=%s image=%s sidecar=%s",
            statefulset_name,
            namespace,
            image,
            sidecar_active,
        )

        try:
            client = self._ensure_client()
            apps_api = AppsV1Api(client)

            # Check if StatefulSet already exists
            try:
                existing_sts = apps_api.read_namespaced_stateful_set(
                    name=statefulset_name, namespace=namespace
                )
                # Exists: scale up
                current_replicas = existing_sts.spec.replicas or 0
                new_replicas = current_replicas + 1
                ordinal = new_replicas - 1
                logger.info(
                    "[real] create_device: scaling StatefulSet %s from %d to %d",
                    statefulset_name,
                    current_replicas,
                    new_replicas,
                )
                apps_api.patch_namespaced_stateful_set_scale(
                    name=statefulset_name,
                    namespace=namespace,
                    body={"spec": {"replicas": new_replicas}},
                )
            except ApiException as e:
                if e.status != 404:
                    raise
                # Does not exist: create new StatefulSet
                ordinal = 0
                logger.info(
                    "[real] create_device: creating new StatefulSet %s with replicas=1",
                    statefulset_name,
                )

                # Build bot-runtime env vars (with optional proxy env vars)
                env_vars = _env_list_from_dict(envs)
                if sidecar_active:
                    env_vars.extend(
                        [
                            V1EnvVar(name="HTTP_PROXY", value="http://localhost:15001"),
                            V1EnvVar(
                                name="HTTPS_PROXY", value="http://localhost:15001"
                            ),
                        ]
                    )

                # Build containers list
                containers = [
                    V1Container(
                        name="bot-runtime",
                        image=image,
                        resources=V1ResourceRequirements(
                            requests={
                                "cpu": cpu_request,
                                "memory": memory_request,
                            },
                            limits={
                                "cpu": cpu_limit,
                                "memory": memory_limit,
                            },
                        ),
                        env=env_vars,
                        ports=[V1ContainerPort(container_port=8080)],
                    )
                ]

                # Build volumes list (empty unless sidecar is active)
                volumes: list[Any] = []

                # Conditionally inject Envoy sidecar + ConfigMap
                if sidecar_active:
                    cm_name = f"{statefulset_name}-proxy-rules"
                    self._create_proxy_configmap(cm_name, namespace, envoy_yaml)

                    containers.append(
                        V1Container(
                            name="envoy-proxy",
                            image="envoyproxy/envoy-alpine:v1.31-latest",
                            command=[
                                "envoy",
                                "-c",
                                "/etc/envoy/proxy-rules/envoy.yaml",
                                "--enable-core-dump",
                            ],
                            volume_mounts=[
                                V1VolumeMount(
                                    name="proxy-config",
                                    mount_path="/etc/envoy/proxy-rules",
                                    read_only=True,
                                )
                            ],
                            resources=V1ResourceRequirements(
                                requests={"cpu": "100m", "memory": "128Mi"},
                                limits={"cpu": "200m", "memory": "256Mi"},
                            ),
                        )
                    )

                    volumes.append(
                        V1Volume(
                            name="proxy-config",
                            config_map=V1ConfigMapVolumeSource(name=cm_name),
                        )
                    )

                statefulset = V1StatefulSet(
                    api_version="apps/v1",
                    kind="StatefulSet",
                    metadata=V1ObjectMeta(
                        name=statefulset_name,
                        namespace=namespace,
                        labels=labels,
                    ),
                    spec=V1StatefulSetSpec(
                        service_name=service_name,
                        replicas=1,
                        selector=V1LabelSelector(
                            match_labels={"app": statefulset_name}
                        ),
                        pod_management_policy="OrderedReady",
                        update_strategy={"type": "RollingUpdate"},
                        template=V1PodTemplateSpec(
                            metadata=V1ObjectMeta(
                                labels={"app": statefulset_name, **labels}
                            ),
                            spec=V1PodSpec(
                                containers=containers,
                                volumes=(volumes if volumes else None),
                            ),
                        ),
                    ),
                )
                apps_api.create_namespaced_stateful_set(
                    namespace=namespace, body=statefulset
                )

            sandbox_id = f"{statefulset_name}-{ordinal}"

            return RealK8sSandbox(
                sandbox_id=sandbox_id,
                namespace=namespace,
                pod=None,
                client=client,
            )
        except ApiException as e:
            raise RuntimeError(f"create_device failed ({e.status})") from e

    def connect_device(self, sandbox_id: str, namespace: str) -> RealK8sSandbox:
        """Connect to an existing sandbox Pod by exact name match.

        Args:
            sandbox_id: Pod name ({statefulset_name}-{ordinal}).
            namespace: K8s namespace.

        Returns:
            RealK8sSandbox wrapping the existing Pod.

        Raises:
            RuntimeError(404): If Pod not found.
        """
        logger.info(
            "[real] connect_device sandbox_id=%s namespace=%s",
            sandbox_id,
            namespace,
        )
        try:
            client = self._ensure_client()
            core_api = CoreV1Api(client)
            pod = core_api.read_namespaced_pod(name=sandbox_id, namespace=namespace)
            return RealK8sSandbox(
                sandbox_id=sandbox_id,
                namespace=namespace,
                pod=pod,
                client=client,
            )
        except ApiException as e:
            raise RuntimeError(
                f"Pod {sandbox_id} not found in namespace {namespace} ({e.status})"
            ) from e

    def list_instances(
        self, namespace: str, label_selector: str | None = None
    ) -> list[dict[str, Any]]:
        """List K8s sandbox instances (StatefulSets) in a namespace.

        Args:
            namespace: K8s namespace to query.
            label_selector: Optional label selector string.

        Returns:
            List of dicts with sandbox_id, status, namespace, replicas, ready_replicas.
        """
        logger.info(
            "[real] list_instances namespace=%s label_selector=%s",
            namespace,
            label_selector,
        )
        try:
            client = self._ensure_client()
            apps_api = AppsV1Api(client)
            sts_list = apps_api.list_namespaced_stateful_set(
                namespace=namespace,
                label_selector=label_selector,
            )
            result = []
            for sts in sts_list.items:
                spec_replicas = sts.spec.replicas or 0
                ready_replicas = sts.status.ready_replicas or 0
                if ready_replicas >= spec_replicas and spec_replicas > 0:
                    status = "RUNNING"
                elif spec_replicas == 0:
                    status = "DESTROYED"
                else:
                    status = "PROVISIONING"

                result.append(
                    {
                        "sandbox_id": sts.metadata.name,
                        "status": status,
                        "namespace": namespace,
                        "replicas": spec_replicas,
                        "ready_replicas": ready_replicas,
                    }
                )
            return result
        except ApiException as e:
            raise RuntimeError(f"list_instances failed ({e.status})") from e

    def resolve_ws_conn_info(
        self, paas_device_id: str, port: int, path: str, namespace: str
    ) -> Any:
        """Resolve WebSocket connection info for a sandbox Pod.

        Parses paas_device_id as "{statefulset_name}--{ordinal}" (D-01 format).
        Performs a fresh read_namespaced_pod() call every time (no IP caching).
        Raises RuntimeError(503) if Pod IP is not yet assigned.
        """
        logger.info(
            "[real] resolve_ws_conn_info device_id=%s port=%d path=%s namespace=%s",
            paas_device_id,
            port,
            path,
            namespace,
        )
        try:
            pod_name = self._parse_pod_name(paas_device_id)
            client = self._ensure_client()
            core_api = CoreV1Api(client)
            pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
            pod_ip = getattr(pod.status, "pod_ip", None)
            if pod_ip is None:
                raise RuntimeError(f"Pod {pod_name} IP not yet assigned (503)")
            normalized_path = "/" + path.lstrip("/")
            ws_url = f"ws://{pod_ip}:{port}{normalized_path}"
            from secbaas.community.api.bot_runtime import WsConnectionInfo

            return WsConnectionInfo(
                ws_url=ws_url,
                token="",
                target=f"{pod_ip}:{port}",
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        except ApiException as e:
            raise RuntimeError(f"resolve_ws_conn_info failed ({e.status})") from e

    def resolve_invoke_http_info(
        self, paas_device_id: str, port: int, path: str, namespace: str
    ) -> Any:
        """Resolve HTTP connection info for a sandbox Pod.

        Same Pod name derivation as resolve_ws_conn_info. Fresh Pod read
        every time (no IP caching). Raises RuntimeError(503) if no Pod IP.
        """
        logger.info(
            "[real] resolve_invoke_http_info device_id=%s port=%d path=%s namespace=%s",
            paas_device_id,
            port,
            path,
            namespace,
        )
        try:
            pod_name = self._parse_pod_name(paas_device_id)
            client = self._ensure_client()
            core_api = CoreV1Api(client)
            pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
            pod_ip = getattr(pod.status, "pod_ip", None)
            if pod_ip is None:
                raise RuntimeError(f"Pod {pod_name} IP not yet assigned (503)")
            normalized_path = "/" + path.lstrip("/")
            http_url = f"http://{pod_ip}:{port}{normalized_path}"
            from secbaas.community.api.bot_runtime import HttpConnectionInfo

            return HttpConnectionInfo(http_url=http_url, token="")
        except ApiException as e:
            raise RuntimeError(f"resolve_invoke_http_info failed ({e.status})") from e

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
        """Invoke an HTTP request directly on a sandbox Pod via httpx.

        Args:
            paas_device_id: Parsed as "{statefulset_name}--{ordinal}".
            method: HTTP method (GET, POST, etc.).
            port: Target port on the Pod.
            path: Request path.
            namespace: K8s namespace.
            query_string: Optional query string (including leading "?").
            headers: Optional HTTP headers dict.
            body: Optional raw request body bytes.

        Returns:
            Dict with status_code, headers, body (base64-encoded).
        """
        logger.info(
            "[real] invoke_http_in_device device_id=%s method=%s port=%d path=%s",
            paas_device_id,
            method,
            port,
            path,
        )
        try:
            pod_name = self._parse_pod_name(paas_device_id)
            client = self._ensure_client()
            core_api = CoreV1Api(client)
            pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
            pod_ip = getattr(pod.status, "pod_ip", None)
            if pod_ip is None:
                raise RuntimeError(f"Pod {pod_name} IP not yet assigned (503)")
        except ApiException as e:
            raise RuntimeError(f"invoke_http_in_device failed ({e.status})") from e

        url = f"http://{pod_ip}:{port}{path}"
        if query_string:
            url += query_string

        try:
            httpx_client = httpx.Client()
            try:
                response = httpx_client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body,
                    timeout=30.0,
                )
                return {
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "body": base64.b64encode(response.content).decode("ascii"),
                }
            finally:
                httpx_client.close()
        except httpx.HTTPError as e:
            raise RuntimeError(f"invoke_http_in_device failed: {e} (502)") from e

    def close(self) -> None:
        """No-op close. K8sClientManager lifecycle is managed by the DI container."""
        logger.info(
            "[real] plugin close (no-op -- K8sClientManager managed by DI container)"
        )

    def update_outbound_operation_rule(
        self, statefulset_name: str, namespace: str, envoy_yaml: str
    ) -> None:
        """Update the Envoy proxy rules ConfigMap (patch or create).

        Matches K8sSandboxPlugin Protocol from Plan 02. Delegates to
        _patch_proxy_configmap with 404-fallback-to-create semantics.
        """
        cm_name = f"{statefulset_name}-proxy-rules"
        logger.info(
            "[real] update_outbound_operation_rule configmap=%s namespace=%s",
            cm_name,
            namespace,
        )
        try:
            self._patch_proxy_configmap(cm_name, namespace, envoy_yaml)
        except ApiException as e:
            raise RuntimeError(
                f"update_outbound_operation_rule failed ({e.status})"
            ) from e

    def _create_proxy_configmap(
        self, name: str, namespace: str, envoy_yaml: str
    ) -> None:
        """Create a ConfigMap for Envoy proxy rules.

        Uses CoreV1Api.create_namespaced_config_map(). Labels identify the
        ConfigMap as managed by secbaas and of component envoy-proxy-config.
        """
        try:
            client = self._ensure_client()
            core_api = CoreV1Api(client)
            cm = V1ConfigMap(
                api_version="v1",
                kind="ConfigMap",
                metadata=V1ObjectMeta(
                    name=name,
                    namespace=namespace,
                    labels={
                        "app.kubernetes.io/managed-by": "secbaas",
                        "app.kubernetes.io/component": "envoy-proxy-config",
                    },
                ),
                data={"envoy.yaml": envoy_yaml},
            )
            core_api.create_namespaced_config_map(namespace=namespace, body=cm)
            logger.info(
                "[real] create_configmap: ConfigMap %s created in namespace %s",
                name,
                namespace,
            )
        except ApiException as e:
            raise RuntimeError(f"create_configmap failed ({e.status})") from e

    def _patch_proxy_configmap(
        self, name: str, namespace: str, envoy_yaml: str
    ) -> None:
        """Patch an existing ConfigMap with updated Envoy config.

        On 404 (ConfigMap does not exist), falls back to creating it
        (first-time call path). On other ApiException errors, raises
        RuntimeError wrapping the status code.
        """
        try:
            client = self._ensure_client()
            core_api = CoreV1Api(client)
            patch_body = {"data": {"envoy.yaml": envoy_yaml}}
            core_api.patch_namespaced_config_map(
                name=name, namespace=namespace, body=patch_body
            )
            logger.info(
                "[real] patch_configmap: ConfigMap %s patched in namespace %s",
                name,
                namespace,
            )
        except ApiException as e:
            if e.status == 404:
                # ConfigMap doesn't exist — create it (first-time call)
                self._create_proxy_configmap(name, namespace, envoy_yaml)
                return
            raise RuntimeError(f"patch_configmap failed ({e.status})") from e

    def _parse_pod_name(self, paas_device_id: str) -> str:
        """Parse paas_device_id into a Pod name.

        Format: "{statefulset_name}--{ordinal}" per D-01.
        Pod name is derived as "{statefulset_name}-{ordinal}".

        Raises:
            RuntimeError(422): If the paas_device_id format is invalid.
        """
        parts = paas_device_id.split("--", maxsplit=1)
        if len(parts) != 2:
            raise RuntimeError(f"Invalid paas_device_id format: {paas_device_id} (422)")
        statefulset_name, ordinal_str = parts
        try:
            ordinal = int(ordinal_str)
        except ValueError:
            raise RuntimeError(
                f"Invalid paas_device_id ordinal: {paas_device_id} (422)"
            ) from None
        if ordinal < 0:
            raise RuntimeError(
                f"Invalid paas_device_id ordinal (must be >= 0): {paas_device_id} (422)"
            ) from None
        return f"{statefulset_name}-{ordinal}"
