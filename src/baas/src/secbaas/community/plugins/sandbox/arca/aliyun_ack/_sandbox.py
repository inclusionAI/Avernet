"""Aliyun ACK-backed ArcaSandbox implementation.

Wraps an ACK (Aliyun managed Kubernetes) Pod behind the ``ArcaSandbox``
protocol. All Kubernetes API calls go through the injected ApiClient.
"""

from __future__ import annotations

import time
from typing import Any

from kubernetes.client import ApiClient, AppsV1Api, CoreV1Api
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream as k8s_stream

from secbaas.community.api.device_manage import (
    OutBoundOperationRule,
    OutBoundOperationRuleUpdatedMode,
)
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.arca import ArcaSandbox, ArcaSandboxInfo

logger = get_logger("plugin-sandbox")


class _ExecResult:
    """Simple CommandResult-like namespace object."""

    exit_code = 0
    stdout = ""
    stderr = ""
    elapsed_time = 0.0


class AliyunAckSandbox(ArcaSandbox):
    """An Arca-compatible sandbox backed by an ACK Pod.

    Implements the ``ArcaSandbox`` Protocol for ACK-managed Kubernetes sandbox
    devices. ``get_info()`` returns the unified :class:`ArcaSandboxInfo`.
    The stable ``sandbox_id`` is the Pod name (not the ephemeral Pod IP).
    """

    def __init__(
        self,
        sandbox_id: str,
        namespace: str,
        template_id: str,
        client: ApiClient,
        *,
        pod_name: str | None = None,
        deployment_name: str | None = None,
        container_name: str | None = None,
        image: str | None = None,
        ttl_in_minutes: float | int | None = None,
    ) -> None:
        self._sandbox_id = sandbox_id
        self._namespace = namespace
        self._template_id = template_id
        self._client = client
        self._pod_name = pod_name or sandbox_id
        self._deployment_name = deployment_name or ""
        self._container_name = container_name
        self._image = image
        self._ttl_in_minutes = ttl_in_minutes

    @property
    def is_ready(self) -> bool:
        """Check whether the backing Pod is in the Running phase."""
        try:
            info = self.get_info()
        except Exception:
            return False
        return info.status == "Running"

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id

    def get_info(self) -> ArcaSandboxInfo:
        """Extract Pod status into the unified ArcaSandboxInfo.

        Reads the absolute expiry deadline from the Pod's
        ``avernet.arcasandbox/ttl_expiration_timestamp`` annotation (ms epoch) and
        reports it as ``ttl_timestamp``. The remaining ``ttl_in_minutes`` is
        derived from that deadline, so the renew path treats an ACK Pod like
        any other Arca sandbox (absolute deadline as source of truth).
        """
        try:
            core_api = CoreV1Api(self._client)
            pod = core_api.read_namespaced_pod(
                name=self._pod_name, namespace=self._namespace
            )
            status = getattr(pod, "status", None)

            ttl_timestamp = self._read_ttl_expiration_timestamp(pod)
            if ttl_timestamp is None and self._ttl_in_minutes is not None:
                ttl_timestamp = self._compute_expiration_from_creation(
                    pod, self._ttl_in_minutes
                )
            ttl_in_minutes = self._remaining_minutes(ttl_timestamp)

            pod_ip = getattr(status, "pod_ip", None)
            if not pod_ip:
                raise RuntimeError(
                    f"get_info failed for sandbox {self._sandbox_id}: pod has no status.pod_ip"
                )

            metadata: dict[str, Any] = {
                "pod_name": self._pod_name,
                "namespace": self._namespace,
                "ip_addr": pod_ip,
            }

            return ArcaSandboxInfo(
                sandbox_id=self._sandbox_id,
                status=getattr(status, "phase", None) or "UNKNOWN",
                template_id=self._template_id,
                ttl_in_minutes=ttl_in_minutes,
                ttl_timestamp=ttl_timestamp,
                metadata=metadata,
            )
        except ApiException as e:
            raise RuntimeError(
                f"get_info failed for sandbox {self._sandbox_id} ({e.status})"
            ) from e

    @staticmethod
    def _read_ttl_expiration_timestamp(pod: Any) -> int | None:
        """Read the absolute expiry deadline (ms epoch) from the Pod annotation.

        Returns ``None`` when the annotation is absent or malformed.
        """
        metadata = getattr(pod, "metadata", None)
        annotations = getattr(metadata, "annotations", None) or {}
        raw = annotations.get("avernet.arcasandbox/ttl_expiration_timestamp")
        if raw is None:
            return None
        try:
            value = int(float(raw))
        except (TypeError, ValueError, OverflowError):
            return None
        return value if value > 0 else None

    @staticmethod
    def _compute_expiration_from_creation(
        pod: Any, ttl_in_minutes: float | int | None
    ) -> int | None:
        """Compute expiry as ``creation_timestamp + ttl_in_minutes`` (ms epoch).

        Backward-compatible fallback for Pods created before the
        ``ttl_expiration_timestamp`` annotation existed. Returns ``None`` when the
        creation time or TTL cannot be resolved.
        """
        from datetime import datetime as _datetime

        if ttl_in_minutes is None:
            return None
        metadata = getattr(pod, "metadata", None)
        creation = getattr(metadata, "creation_timestamp", None) if metadata else None
        if not isinstance(creation, _datetime):
            return None
        import calendar

        created_epoch_s = calendar.timegm(creation.utctimetuple())
        return int((created_epoch_s + ttl_in_minutes * 60) * 1000)

    @staticmethod
    def _remaining_minutes(ttl_timestamp: int | float | None) -> float | int | None:
        """Derive remaining TTL minutes from an absolute expiry (ms epoch).

        Returns ``None`` when the expiry is unknown; a zero/negative remaining
        value means the deadline has already passed.
        """
        if ttl_timestamp is None:
            return None
        remaining_ms = float(ttl_timestamp) - time.time() * 1000
        return remaining_ms / 60000.0

    def destroy(self) -> bool:
        """Delete the backing Deployment and associated resources. Idempotent on 404.

        Removes the Deployment, ConfigMap, and NetworkPolicy created from
        the template. The PVC is NOT deleted — it is an independent resource
        so data survives Deployment deletion.
        """
        logger.info(
            "[aliyun_ack] destroy sandbox_id=%s deployment=%s",
            self._sandbox_id,
            self._deployment_name,
        )
        uid = self._deployment_name.removeprefix("avernet-agent-")
        core_api = CoreV1Api(self._client)
        for name, delete_fn in (
            (
                self._deployment_name,
                lambda: AppsV1Api(self._client).delete_namespaced_deployment(
                    name=self._deployment_name, namespace=self._namespace
                ),
            ),
            (
                f"envoy-header-rules-{uid}",
                lambda: core_api.delete_namespaced_config_map(
                    name=f"envoy-header-rules-{uid}", namespace=self._namespace
                ),
            ),
            (
                f"avernet-agent-netpol-{uid}",
                lambda: core_api.delete_namespaced_network_policy(
                    name=f"avernet-agent-netpol-{uid}", namespace=self._namespace
                ),
            ),
        ):
            try:
                delete_fn()
            except ApiException as e:
                if e.status != 404:
                    raise RuntimeError(f"destroy failed ({e.status})") from e
                logger.info("[aliyun_ack] destroy: %s already gone (404)", name)
        return True

    def exec_command(
        self,
        cmd: str,
        timeout_in_millis: int = 30000,
        envs: dict[str, str] | None = None,
    ) -> Any:
        """Execute a command inside the backing ACK Pod via exec."""
        logger.info(
            "[aliyun_ack] exec_command sandbox_id=%s timeout=%d cmd=%s",
            self._sandbox_id,
            timeout_in_millis,
            cmd[:200],
        )
        started = time.monotonic()
        try:
            core_api = CoreV1Api(self._client)
            resp = k8s_stream(
                core_api.connect_get_namespaced_pod_exec,
                name=self._pod_name,
                namespace=self._namespace,
                container=self._container_name,
                command=["/bin/sh", "-c", cmd],
                stderr=True,
                stdout=True,
                stdin=False,
                tty=False,
                _preload_content=False,
            )
            resp.run_forever(timeout=timeout_in_millis / 1000.0)
        except ApiException as e:
            raise RuntimeError(f"exec_command failed ({e.status}): {e.body}") from e

        elapsed = time.monotonic() - started
        result = _ExecResult()
        result.exit_code = resp.returncode if resp.returncode is not None else 0
        result.stdout = resp.read_stdout() or ""
        result.stderr = resp.read_stderr() or ""
        result.elapsed_time = elapsed
        return result

    def update_outbound_rule(
        self,
        rule: OutBoundOperationRule,
        updated_mode: OutBoundOperationRuleUpdatedMode,
    ) -> Any:
        raise NotImplementedError("update_outbound_rule not implemented")

    def extend_ttl(self, ttl_minutes: int) -> Any:
        """Extend the Pod's absolute expiry deadline by ``ttl_minutes``.

        Re-reads the current deadline from the ``ttl_expiration_timestamp``
        annotation (falling back to now) and records ``deadline + ttl_minutes``
        as the new annotation value.
        """
        logger.info(
            "[aliyun_ack] extend_ttl sandbox_id=%s ttl_minutes=%d",
            self._sandbox_id,
            ttl_minutes,
        )
        try:
            core_api = CoreV1Api(self._client)
            pod = core_api.read_namespaced_pod(
                name=self._pod_name, namespace=self._namespace
            )
            current = self._read_ttl_expiration_timestamp(pod)
            base_ms = current if current is not None else time.time() * 1000
            new_expiration = int(base_ms + ttl_minutes * 60 * 1000)
            annotations = {
                "avernet.arcasandbox/ttl_expiration_timestamp": str(new_expiration),
            }
            core_api.patch_namespaced_pod(
                name=self._pod_name,
                namespace=self._namespace,
                body={
                    "metadata": {"annotations": annotations},
                },
            )
            return True
        except ApiException as e:
            raise RuntimeError(f"extend_ttl failed ({e.status})") from e
