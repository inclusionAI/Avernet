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

        Also derives ``ttl_timestamp`` (ms epoch) from the Pod's
        ``metadata.creation_timestamp`` plus the
        ``avernet.arcasandbox/ttl-minutes`` annotation. This lets the renew path
        treat an ACK Pod like any other Arca sandbox and extend its TTL.
        """
        try:
            core_api = CoreV1Api(self._client)
            pod = core_api.read_namespaced_pod(
                name=self._pod_name, namespace=self._namespace
            )
            status = getattr(pod, "status", None)

            ttl_in_minutes = self._ttl_in_minutes
            ttl_timestamp = self._derive_ttl_timestamp(pod, ttl_in_minutes)

            return ArcaSandboxInfo(
                sandbox_id=self._sandbox_id,
                status=getattr(status, "phase", None) or "UNKNOWN",
                template_id=self._template_id,
                ttl_in_minutes=ttl_in_minutes,
                ttl_timestamp=ttl_timestamp,
                metadata={
                    "pod_name": self._pod_name,
                    "namespace": self._namespace,
                },
            )
        except ApiException as e:
            raise RuntimeError(
                f"get_info failed for sandbox {self._sandbox_id} ({e.status})"
            ) from e

    def _derive_ttl_timestamp(
        self, pod: Any, ttl_in_minutes: float | int | None
    ) -> int | None:
        """Compute the Pod expiry as a ms epoch timestamp.

        Expiry = pod ``metadata.creation_timestamp`` + ``ttl-minutes`` annotation
        (falling back to ``self._ttl_in_minutes``). Returns ``None`` when either
        the creation time or the TTL cannot be resolved, so callers can fall back
        to their own defaults rather than assuming a value.
        """
        from datetime import datetime as _datetime

        metadata = getattr(pod, "metadata", None)
        creation = getattr(metadata, "creation_timestamp", None) if metadata else None
        if not isinstance(creation, _datetime):
            return None

        ttl = ttl_in_minutes
        if ttl is None and metadata is not None:
            annotations = getattr(metadata, "annotations", None) or {}
            raw_ttl = annotations.get("avernet.arcasandbox/ttl-minutes")
            if raw_ttl is not None:
                try:
                    ttl = float(raw_ttl)
                except (TypeError, ValueError):
                    ttl = None
        if ttl is None:
            return None

        import calendar

        created_epoch_s = calendar.timegm(creation.utctimetuple())
        return int((created_epoch_s + ttl * 60) * 1000)

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
        """Extend the Pod TTL annotation by ``ttl_minutes``."""
        logger.info(
            "[aliyun_ack] extend_ttl sandbox_id=%s ttl_minutes=%d",
            self._sandbox_id,
            ttl_minutes,
        )
        annotations = {
            "avernet.arcasandbox/ttl-minutes": str(ttl_minutes),
        }
        try:
            core_api = CoreV1Api(self._client)
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
