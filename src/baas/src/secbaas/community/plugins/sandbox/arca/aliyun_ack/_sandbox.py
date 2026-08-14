"""Aliyun ACK-backed ArcaSandbox implementation.

Wraps an ACK (Aliyun managed Kubernetes) Pod behind the ``ArcaSandbox``
protocol. All Kubernetes API calls go through the injected ApiClient.

Kubernetes SDK classes are lazily bound to this module so unit tests can patch
``_sandbox.CoreV1Api`` / ``_sandbox.ApiException`` via ``unittest.mock``.
"""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, Any

from secbaas.community.api.device_manage import (
    OutBoundOperationRule,
    OutBoundOperationRuleUpdatedMode,
)
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.arca import ArcaSandbox, ArcaSandboxInfo

if TYPE_CHECKING:
    from kubernetes.client import ApiClient, CoreV1Api
    from kubernetes.client.rest import ApiException

logger = get_logger("plugin-sandbox-arca-aliyun-ack")


def _import_k8s() -> None:
    """Lazily bind Kubernetes SDK classes into this module namespace."""
    _mod = sys.modules[__name__]
    if getattr(_mod, "_k8s_loaded", False):
        return
    from kubernetes.client import CoreV1Api as _CoreV1Api
    from kubernetes.client.rest import ApiException as _ApiException

    _mod.CoreV1Api = _CoreV1Api
    _mod.ApiException = _ApiException
    _mod._k8s_loaded = True


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
        container_name: str = "sandbox",
        image: str = "ubuntu:22.04",
    ) -> None:
        self._sandbox_id = sandbox_id
        self._namespace = namespace
        self._template_id = template_id
        self._client = client
        self._pod_name = pod_name or sandbox_id
        self._container_name = container_name
        self._image = image

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
        """Extract Pod status into the unified ArcaSandboxInfo."""
        _import_k8s()
        try:
            core_api = CoreV1Api(self._client)
            pod = core_api.read_namespaced_pod(
                name=self._pod_name, namespace=self._namespace
            )
            status = getattr(pod, "status", None)
            return ArcaSandboxInfo(
                sandbox_id=self._sandbox_id,
                status=getattr(status, "phase", None) or "UNKNOWN",
                template_id=self._template_id,
                metadata={
                    "pod_name": self._pod_name,
                    "namespace": self._namespace,
                },
            )
        except ApiException as e:
            raise RuntimeError(
                f"get_info failed for sandbox {self._sandbox_id} ({e.status})"
            ) from e

    def destroy(self) -> bool:
        """Delete the backing Pod. Idempotent on 404."""
        _import_k8s()
        logger.info(
            "[aliyun_ack] destroy sandbox_id=%s pod=%s",
            self._sandbox_id,
            self._pod_name,
        )
        try:
            core_api = CoreV1Api(self._client)
            core_api.delete_namespaced_pod(
                name=self._pod_name, namespace=self._namespace
            )
            return True
        except ApiException as e:
            if e.status == 404:
                logger.info(
                    "[aliyun_ack] destroy: pod %s already gone (404), idempotent",
                    self._pod_name,
                )
                return True
            raise RuntimeError(f"destroy failed ({e.status})") from e

    def exec_command(
        self,
        cmd: str,
        timeout_in_millis: int = 30000,
        envs: dict[str, str] | None = None,
    ) -> Any:
        """Execute a command inside the backing ACK Pod via exec."""
        _import_k8s()
        logger.info(
            "[aliyun_ack] exec_command sandbox_id=%s timeout=%d cmd=%s",
            self._sandbox_id,
            timeout_in_millis,
            cmd[:200],
        )
        started = time.monotonic()
        try:
            core_api = CoreV1Api(self._client)
            resp = core_api.connect_get_namespaced_pod_exec(
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
            raise RuntimeError(f"exec_command failed ({e.status})") from e

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
        """Apply an outbound rule to the backing Pod. Only REPLACE is supported."""
        if updated_mode != OutBoundOperationRuleUpdatedMode.REPLACE:
            raise NotImplementedError(
                "ACK sandbox only supports REPLACE outbound rule updates, "
                f"got {updated_mode}"
            )
        _import_k8s()
        import json

        annotations = {
            "avernet.arcasandbox/outbound-rule": json.dumps(
                rule.model_dump(exclude_none=True)
                if hasattr(rule, "model_dump")
                else {}
            )
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
            raise RuntimeError(f"update_outbound_rule failed ({e.status})") from e

    def extend_ttl(self, ttl_minutes: int) -> Any:
        """Extend the Pod TTL annotation by ``ttl_minutes``."""
        _import_k8s()
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
