"""本地 K8s Arca 沙箱实现。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import yaml

from secbaas.community.api.device_manage import (
    OutBoundOperationRule,
    OutBoundOperationRuleUpdatedMode,
    ResourceSpecification,
)
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.arca import ArcaSandbox, ArcaSandboxInfo

if TYPE_CHECKING:
    from kubernetes.client import ApiClient

    from secbaas.community.api.device_manage import ArcaCredentials

logger = get_logger("plugin-sandbox")

_SIDECAR_ADMIN_PORT = 38081


def _convert_outbound_rules(rule: OutBoundOperationRule | None) -> str:
    """把 OutBoundOperationRule 转成 header-rules.yaml 文本。"""
    if not rule or not rule.header_operation_rules:
        return "rules: []"

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
            set_entry: dict[str, Any] = {"header": h.header_name, "value": h.value}
            if h.placeholder:
                set_entry["placeholder"] = h.placeholder
            domain_groups[key]["set"].append(set_entry)
        elif action == "remove":
            domain_groups[key]["remove"].append(h.header_name)

    return yaml.safe_dump(
        {"rules": list(domain_groups.values())},
        default_flow_style=False,
        sort_keys=False,
    )


class _ExecResult:
    """命令执行结果占位对象。"""

    def __init__(
        self,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        elapsed_time: float = 0.0,
    ) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.elapsed_time = elapsed_time


class LocalK8sArcaSandbox(ArcaSandbox):
    """运行在本地 Kubernetes Pod 里的 Arca 沙箱。"""

    def __init__(
        self,
        sandbox_id: str,
        pod_name: str,
        namespace: str,
        template_id: str,
        client: ApiClient,
        container_name: str,
        credentials: ArcaCredentials | None = None,
        ttl_in_minutes: int | None = None,
        resources: ResourceSpecification | None = None,
        sidecar_container_name: str | None = None,
    ) -> None:
        self._sandbox_id = sandbox_id
        self._pod_name = pod_name
        self._namespace = namespace
        self._template_id = template_id
        self._client = client
        self._container_name = container_name
        self._credentials = credentials
        self._ttl_in_minutes = ttl_in_minutes
        self._resources = resources
        self._sidecar_container_name = sidecar_container_name

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id

    @property
    def _header_rules_configmap_name(self) -> str:
        return f"envoy-header-rules-{self._sandbox_id}"

    @property
    def is_ready(self) -> bool:
        """检查底层 Pod 是否 Running。"""
        try:
            info = self.get_info()
        except Exception:
            return False
        return str(getattr(info, "status", "")).lower() == "running"

    def _read_pod(self) -> Any:
        from kubernetes.client import CoreV1Api

        core_api = CoreV1Api(self._client)
        return core_api.read_namespaced_pod(
            name=self._pod_name, namespace=self._namespace
        )

    def get_info(self) -> ArcaSandboxInfo:
        """把 Pod 状态转成 ArcaSandboxInfo。"""
        pod = self._read_pod()
        status = getattr(pod.status, "phase", "UNKNOWN")
        pod_ip = getattr(pod.status, "pod_ip", None) if pod.status else None
        metadata = {
            "pod_name": self._pod_name,
            "namespace": self._namespace,
            "pod_ip": pod_ip,
            # ip_addr 与 aliyun_ack 对齐：sandbox proxy 的 ARCA resolver 从
            # provider_device_props.metadata.ip_addr 读取 Pod IP。
            "ip_addr": pod_ip,
            "container_name": self._container_name,
        }
        resources = self._resources

        return ArcaSandboxInfo(
            sandbox_id=self._sandbox_id,
            status=status,
            template_id=self._template_id,
            ttl_in_minutes=self._ttl_in_minutes,
            metadata=metadata,
            resources=resources,
            is_ready=self.is_ready,
        )

    def destroy(self) -> Any:
        """删除 Deployment、Service 和 header-rules ConfigMap。"""
        from kubernetes.client import ApiException, AppsV1Api, CoreV1Api

        apps_api = AppsV1Api(self._client)
        core_api = CoreV1Api(self._client)
        service_name = f"{self._sandbox_id}-svc"

        for api, name, kind in [
            (apps_api.delete_namespaced_deployment, self._sandbox_id, "deployment"),
            (core_api.delete_namespaced_service, service_name, "service"),
            (
                core_api.delete_namespaced_config_map,
                self._header_rules_configmap_name,
                "configmap",
            ),
        ]:
            try:
                api(name=name, namespace=self._namespace)
                logger.info("local_k8s: deleted %s %s/%s", kind, self._namespace, name)
            except ApiException as e:
                if e.status != 404:
                    raise RuntimeError(
                        f"local_k8s: destroy {kind} failed ({e.status})"
                    ) from e
                logger.info(
                    "local_k8s: %s %s/%s already gone", kind, self._namespace, name
                )
        return True

    def _exec_in_container(
        self,
        container_name: str,
        command: list[str],
        timeout_in_millis: int = 30000,
    ) -> _ExecResult:
        """在一个容器内执行命令并返回退出码/输出。"""
        from kubernetes.client import CoreV1Api
        from kubernetes.client.rest import ApiException
        from kubernetes.stream import stream as k8s_stream

        started = time.monotonic()
        try:
            core_api = CoreV1Api(self._client)
            resp = k8s_stream(
                core_api.connect_get_namespaced_pod_exec,
                name=self._pod_name,
                namespace=self._namespace,
                container=container_name,
                command=command,
                stderr=True,
                stdout=True,
                stdin=False,
                tty=False,
                _preload_content=False,
            )
            resp.run_forever(timeout=timeout_in_millis / 1000.0)
            elapsed = time.monotonic() - started
            return _ExecResult(
                exit_code=resp.returncode if resp.returncode is not None else 0,
                stdout=resp.read_stdout() or "",
                stderr=resp.read_stderr() or "",
                elapsed_time=elapsed,
            )
        except ApiException as e:
            logger.warning(
                "local_k8s: exec failed in %s (%s): %s",
                container_name,
                e.status,
                e.body,
            )
            return _ExecResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                elapsed_time=time.monotonic() - started,
            )

    def exec_command(
        self,
        cmd: str,
        timeout_in_millis: int = 30000,
        envs: dict[str, str] | None = None,
    ) -> Any:
        """在主容器里执行命令。

        本地开发阶段失败时返回空结果，不打断上层流程。
        """
        logger.info(
            "local_k8s: exec_command pod=%s/%s cmd=%s",
            self._namespace,
            self._pod_name,
            cmd[:200],
        )
        return self._exec_in_container(
            container_name=self._container_name,
            command=["/bin/sh", "-c", cmd],
            timeout_in_millis=timeout_in_millis,
        )

    def _require_sidecar_container_name(self) -> str:
        if not self._sidecar_container_name:
            raise RuntimeError(
                f"local_k8s: no sidecar container for sandbox {self._sandbox_id}"
            )
        return self._sidecar_container_name

    def _restart_sidecar(self) -> None:
        """只重启 envoy sidecar 容器，不替换整个 Pod。"""
        sidecar_container_name = self._require_sidecar_container_name()
        result = self._exec_in_container(
            container_name=sidecar_container_name,
            command=[
                "curl",
                "-fsS",
                "-X",
                "POST",
                f"http://127.0.0.1:{_SIDECAR_ADMIN_PORT}/quitquitquit",
            ],
            timeout_in_millis=5000,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                "local_k8s: failed to restart sidecar "
                f"{sidecar_container_name}: {result.stderr or result.stdout}"
            )

    def _wait_for_sidecar_ready(self, timeout_seconds: float = 15.0) -> None:
        """等待 sidecar 的 Envoy admin /ready 恢复。"""
        sidecar_container_name = self._require_sidecar_container_name()
        deadline = time.monotonic() + timeout_seconds
        last_error = "no readiness probe executed"
        while time.monotonic() < deadline:
            result = self._exec_in_container(
                container_name=sidecar_container_name,
                command=[
                    "curl",
                    "-fsS",
                    f"http://127.0.0.1:{_SIDECAR_ADMIN_PORT}/ready",
                ],
                timeout_in_millis=2000,
            )
            if result.exit_code == 0:
                return
            last_error = result.stderr or result.stdout or str(result.exit_code)
            time.sleep(0.5)
        raise RuntimeError(
            f"local_k8s: sidecar was not ready within {timeout_seconds}s: {last_error}"
        )

    def update_outbound_rule(
        self,
        rule: OutBoundOperationRule,
        updated_mode: OutBoundOperationRuleUpdatedMode,
    ) -> Any:
        """更新出站规则并原地刷新 envoy sidecar，不滚动 Deployment。"""
        from kubernetes.client import CoreV1Api

        core_api = CoreV1Api(self._client)
        configmap_name = self._header_rules_configmap_name
        core_api.patch_namespaced_config_map(
            name=configmap_name,
            namespace=self._namespace,
            body={
                "data": {
                    "header-rules.yaml": _convert_outbound_rules(rule),
                }
            },
        )
        logger.info(
            "local_k8s: patched configmap %s/%s", self._namespace, configmap_name
        )

        self._restart_sidecar()
        self._wait_for_sidecar_ready()
        logger.info(
            "local_k8s: refreshed sidecar %s/%s/%s without rolling deployment",
            self._namespace,
            self._sandbox_id,
            self._sidecar_container_name,
        )
        return True

    def extend_ttl(self, ttl_minutes: int) -> Any:
        """本地模式下 TTL 延长暂不实现，返回成功。"""
        logger.info("local_k8s: extend_ttl %s minutes (no-op)", ttl_minutes)
        self._ttl_in_minutes = (self._ttl_in_minutes or 0) + ttl_minutes
        return True
