"""Explicit acceptance JobWorkerSandbox on Kubernetes; no platform SDK fallback.

create schedules a single-use Pod with a fixed, image-baked worker entrypoint;
exec validates that command and waits for its exit (NOT a remote shell/exec).
A default-deny policy is installed before scheduling and kept until Pod removal.
The deployment must supply an enforcing CNI; manifests alone do not prove that.
The injected client owns API credentials; none are forwarded to the worker.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import secrets
import shlex
from urllib.parse import urlsplit

from avernet_evolution.core.evolution_run.sandbox import JobWorkerSpec

SMOKE_COMMAND = "python -m avernet_evolution.plugins.smoke_entrypoint"
WORKER_COMMAND = "python -m avernet_evolution.plugins.entrypoint"


class KubernetesJobWorker:
    """Worker-specific port, deliberately NOT a SandboxRuntimeClient implementation."""

    def __init__(
        self,
        client,
        *,
        namespace: str,
        command: str = WORKER_COMMAND,
        image_pull_secrets: tuple[str, ...] = (),
        request_cpu: str = "1",
        request_memory_mb: int = 256,
    ):
        if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?", namespace):
            raise ValueError("invalid worker namespace")
        for name in image_pull_secrets:
            # Secret names only; the registry credential never enters this code.
            if not re.fullmatch(r"[a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?", name):
                raise ValueError("invalid image pull secret name")
        self.client, self.namespace, self.command = client, namespace, command
        self.image_pull_secrets = tuple(image_pull_secrets)
        if not re.fullmatch(r"[0-9]+m?", request_cpu):
            raise ValueError("invalid worker CPU request")
        if request_memory_mb <= 0:
            raise ValueError("worker memory request must be positive")
        self.request_cpu, self.request_memory_mb = request_cpu, request_memory_mb
        self.evidence: dict[str, dict] = {}

    def _path(self, resource: str, name: str = "") -> str:
        if name and not re.fullmatch(r"rsi-[a-f0-9]{16}", name):
            raise ValueError("invalid sandbox id")
        api = "/apis/networking.k8s.io/v1" if resource == "networkpolicies" else "/api/v1"
        return f"{api}/namespaces/{self.namespace}/{resource}" + (f"/{name}" if name else "")

    async def _request(self, method, resource, name="", *, body=None, missing_ok=False):
        response = await self.client.request(method, self._path(resource, name), json=body)
        if missing_ok and response.status_code == 404:
            return None
        # Do not include API bodies (may echo worker credentials) in exceptions.
        if response.status_code >= 400:
            raise RuntimeError(f"Kubernetes {method} {resource} failed ({response.status_code})")
        return response.json()

    async def create(self, spec: JobWorkerSpec) -> str:
        if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", spec.image):
            raise ValueError("worker image must be digest pinned")
        endpoint = urlsplit(spec.job_endpoint)
        if endpoint.scheme not in ("http", "https") or endpoint.username or endpoint.password:
            raise ValueError("worker endpoint must be http(s) without userinfo")
        # No DNS egress, wildcard host, proxy or broad CIDR exception in this adapter.
        address = ipaddress.ip_address(endpoint.hostname or "")
        if address.is_loopback or address.is_unspecified or address.is_multicast:
            raise ValueError("worker endpoint must be a reachable unicast IP")
        port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
        if not spec.rootfs_readonly or spec.egress != (spec.job_endpoint,):
            raise ValueError("readonly root and exact endpoint egress are required")
        if min(spec.cpu, spec.memory, spec.ttl_minutes) <= 0:
            raise ValueError("worker resource limits must be positive")
        name = "rsi-" + secrets.token_hex(8)
        labels = {"app.kubernetes.io/name": "evolution-worker", "evolution-sandbox": name}
        metadata = {"name": name, "labels": labels}
        policy = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": metadata,
            "spec": {
                "podSelector": {"matchLabels": {"evolution-sandbox": name}},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [
                    {
                        "to": [{"ipBlock": {"cidr": f"{address}/{address.max_prefixlen}"}}],
                        "ports": [{"protocol": "TCP", "port": port}],
                    }
                ],
            },
        }
        pod = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": metadata,
            "spec": {
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
                **(
                    {"imagePullSecrets": [{"name": name} for name in self.image_pull_secrets]}
                    if self.image_pull_secrets
                    else {}
                ),
                "enableServiceLinks": False,
                "activeDeadlineSeconds": spec.ttl_minutes * 60,
                "terminationGracePeriodSeconds": 3,
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 65532,
                    "runAsGroup": 65532,
                    "fsGroup": 65532,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "worker",
                        "image": spec.image,
                        "imagePullPolicy": "IfNotPresent",
                        "command": shlex.split(self.command),
                        "env": [{"name": k, "value": v} for k, v in spec.envs().items()],
                        "securityContext": {
                            "readOnlyRootFilesystem": True,
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        # Acceptance-only worker: reserve a small footprint on the
                        # shared simulator, while preserving the spec's hard limits.
                        "resources": {
                            "requests": {
                                "cpu": self.request_cpu,
                                "memory": f"{self.request_memory_mb}Mi",
                            },
                            "limits": {"cpu": str(spec.cpu), "memory": f"{spec.memory}Mi"},
                        },
                        "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                    }
                ],
                "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}],
            },
        }
        try:
            await self._request("POST", "networkpolicies", body=policy)
            created = await self._request("POST", "pods", body=pod)
            self.evidence[name] = {
                "namespace": self.namespace,
                "pod": name,
                "uid": created["metadata"]["uid"],
                "image": spec.image,
                "security_context": pod["spec"]["securityContext"],
                "container_security_context": pod["spec"]["containers"][0]["securityContext"],
                "injected_env_keys": sorted(spec.envs()),
                "network_policy": policy["spec"],
                "automount_service_account_token": False,
                "image_pull_secrets": list(self.image_pull_secrets),
            }
            return name
        except BaseException:
            # Name is known before POST, including lost/unknown create responses.
            await self.destroy(name)
            raise

    async def exec(self, sandbox_id: str, command: str, *, timeout_ms: int) -> int:
        if shlex.split(command) != shlex.split(self.command):
            raise ValueError("this single-use Pod only executes its fixed entrypoint")
        async with asyncio.timeout(timeout_ms / 1000):
            while True:
                pod = await self._request("GET", "pods", sandbox_id)
                status = pod.get("status", {})
                containers = status.get("containerStatuses", [])
                if containers:
                    worker = containers[0]
                    terminated = worker.get("state", {}).get("terminated")
                    if terminated is not None:
                        self.evidence[sandbox_id].update(
                            image_id=worker.get("imageID"),
                            phase=status.get("phase"),
                            exit_code=terminated["exitCode"],
                            pod_ip=status.get("podIP"),
                        )
                        return terminated["exitCode"]
                if status.get("phase") == "Failed":
                    raise RuntimeError("worker Pod failed before container exit")
                await asyncio.sleep(0.2)

    async def destroy(self, sandbox_id: str) -> None:
        await self._request("DELETE", "pods", sandbox_id, missing_ok=True, body={"gracePeriodSeconds": 3})
        # Keep network isolation until actual termination, not just delete acceptance.
        async with asyncio.timeout(30):
            while await self._request("GET", "pods", sandbox_id, missing_ok=True) is not None:
                await asyncio.sleep(0.2)
        await self._request("DELETE", "networkpolicies", sandbox_id, missing_ok=True)
        if sandbox_id in self.evidence:
            self.evidence[sandbox_id]["destroyed"] = True
